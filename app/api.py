"""HTTP-слой: загрузка, статус, SSE-прогресс, отчёт."""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .config import (ALLOWED_EXTENSIONS, MAX_FILE_MB, ROLES, STORAGE_DIR,
                     VISION_MODE, current_bundle, key_diagnostics, role_model,
                     thesis_summary)
from .models import Document, Report, Run, init_db, session_scope
from .pipeline import _snapshot, analyze, subscribe, unsubscribe

app = FastAPI(title="AG Ventures Deck Intelligence")
STATIC = Path(__file__).resolve().parent / "static"


@app.on_event("startup")
def _startup() -> None:
    init_db()


@app.get("/api/v1/config")
def get_config() -> dict:
    return {"thesis": thesis_summary(),
            "formats": sorted(ALLOWED_EXTENSIONS),
            "max_file_mb": MAX_FILE_MB}


@app.get("/api/v1/health")
async def health() -> dict:
    """Проверка ключа и связи с моделью до загрузки первого дека."""
    diag = key_diagnostics()
    result = {"config": diag, "providers": {}}
    for provider_name in {role_model(r)[0] for r in ROLES}:
        try:
            from .llm.blocks import text_block
            from .llm.providers import get_provider
            role = next(r for r in ROLES if role_model(r)[0] == provider_name)
            _, model = role_model(role)
            await get_provider(provider_name).complete(
                model, "Ответь одним словом.", [text_block("ping")], None, 8)
            result["providers"][provider_name] = {"ok": True, "model": model}
        except Exception as e:
            entry = {"ok": False, "model": locals().get("model"), "error": str(e)[:300]}
            provider = get_provider(provider_name)
            if hasattr(provider, "available_models"):
                entry["available_models"] = await provider.available_models()
            result["providers"][provider_name] = entry
    return result


@app.get("/api/v1/providers")
def providers() -> dict:
    """Куда фактически уходит каждый вызов. Первое, что стоит открыть
    после смены провайдера: маршрутизация видна целиком."""
    return {
        "vision_mode": VISION_MODE,
        "roles": {r: "{}:{}".format(*role_model(r)) for r in ROLES},
    }


@app.post("/api/v1/documents")
async def upload(file: UploadFile, force: bool = False) -> dict:
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"Формат {ext} не поддерживается. Загрузите .pdf или .pptx")
    data = await file.read()
    if len(data) > MAX_FILE_MB * 1024 * 1024:
        raise HTTPException(400, f"Файл больше {MAX_FILE_MB} МБ")

    digest = hashlib.sha256(data).hexdigest()
    bundle = current_bundle()
    duplicate = False

    with session_scope() as s:
        doc = s.query(Document).filter_by(sha256=digest).first()

        if doc and not force:
            # Прошлый прогон переиспользуем, только если он чего-то достиг
            # И считался тем же провайдером. Иначе пользователь меняет
            # настройку, видит старый результат и думает, что она не работает.
            # Демо-прогоны не переиспользуются никогда: это синтетика.
            prev = (s.query(Run).filter_by(document_id=doc.id,
                                           provider_bundle=bundle)
                    .filter(Run.status.in_(("completed", "partial")))
                    .order_by(Run.started_at.desc()).first())
            if prev and not bundle.startswith("mock"):
                return {"run_id": prev.id, "duplicate": True, "reused": True,
                        "message": f"Файл уже анализировался на {bundle}, открыт "
                                   f"прошлый прогон. Чтобы пересчитать, нажмите "
                                   f"«Проанализировать заново»"}
            duplicate = True

        if not doc:
            doc = Document(filename=file.filename, ext=ext, size=len(data), sha256=digest)
            s.add(doc)
            s.flush()
            path = STORAGE_DIR / "originals" / f"{doc.id}{ext}"
            path.write_bytes(data)
            doc.storage_key = str(path)

        run = Run(document_id=doc.id, provider_bundle=bundle)
        s.add(run)
        s.flush()
        run_id = run.id

    asyncio.create_task(analyze(run_id))
    return {"run_id": run_id, "duplicate": duplicate,
            "message": "Файл уже загружался, запущен новый прогон" if duplicate else ""}


@app.get("/api/v1/runs/{run_id}")
def get_run(run_id: str) -> dict:
    snap = _snapshot(run_id)
    if snap.get("status") == "unknown":
        raise HTTPException(404, "Прогон не найден")
    return snap


@app.get("/api/v1/runs")
def list_runs() -> list[dict]:
    with session_scope() as s:
        runs = s.query(Run).order_by(Run.started_at.desc()).limit(50).all()
        ids = [r.id for r in runs]
    return [_snapshot(i) for i in ids]


@app.get("/api/v1/runs/{run_id}/events")
async def events(run_id: str) -> StreamingResponse:
    q = subscribe(run_id)

    async def stream():
        try:
            yield f"data: {json.dumps(_snapshot(run_id), ensure_ascii=False)}\n\n"
            while True:
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=20)
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    if payload.get("status") in ("completed", "partial", "failed",
                                                 "not_a_deck", "insufficient_input"):
                        break
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            unsubscribe(run_id, q)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.get("/api/v1/runs/{run_id}/report.html", response_class=HTMLResponse)
def report_html(run_id: str) -> HTMLResponse:
    with session_scope() as s:
        rep = s.query(Report).filter_by(run_id=run_id).first()
        if not rep:
            raise HTTPException(404, "Отчёт ещё не готов")
        key = rep.html_key
    return HTMLResponse(Path(key).read_text(encoding="utf-8"))


@app.get("/api/v1/runs/{run_id}/report.pdf")
def report_pdf(run_id: str) -> FileResponse:
    from .report.render import export_pdf
    with session_scope() as s:
        rep = s.query(Report).filter_by(run_id=run_id).first()
        if not rep:
            raise HTTPException(404, "Отчёт ещё не готов")
        key = rep.html_key
    pdf = export_pdf(key)
    if not pdf:
        raise HTTPException(
            501, "Playwright не установлен. Откройте HTML-версию и напечатайте её в PDF")
    return FileResponse(pdf, filename=f"{run_id}.pdf")


@app.get("/api/v1/pages/{doc_id}/{index}")
def page_image(doc_id: str, index: int) -> FileResponse:
    path = STORAGE_DIR / "pages" / doc_id / f"{index:03d}.png"
    if not path.exists():
        raise HTTPException(404, "Страница не найдена")
    return FileResponse(path)


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
