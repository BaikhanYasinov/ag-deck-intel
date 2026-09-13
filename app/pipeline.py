"""Оркестрация анализа: этапы, параллельные агенты, события прогресса."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime

from .agents import build_onepager, classify_document, run_dimension_agent, structurize
from .config import CONCURRENCY
from .extract import extract_document
from .models import AgentResult, Document, Page, Report, Run, Score, session_scope
from .report.render import render_report
from .rubric import (DIMENSIONS, PHASE0_AGENTS, VERDICT_LABELS, compute_overall,
                     decide_verdict)

# Шина событий: run_id -> подписчики (SSE)
BUS: dict[str, list[asyncio.Queue]] = {}
_sem = asyncio.Semaphore(CONCURRENCY)


def subscribe(run_id: str) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue()
    BUS.setdefault(run_id, []).append(q)
    return q


def unsubscribe(run_id: str, q: asyncio.Queue) -> None:
    if run_id in BUS and q in BUS[run_id]:
        BUS[run_id].remove(q)


def emit(run_id: str, payload: dict) -> None:
    for q in BUS.get(run_id, []):
        q.put_nowait(payload)


def _snapshot(run_id: str) -> dict:
    """Полное состояние прогона. Одна и та же форма для SSE и для GET."""
    with session_scope() as s:
        run = s.get(Run, run_id)
        if not run:
            return {"run_id": run_id, "status": "unknown"}
        doc = s.get(Document, run.document_id)
        results = s.query(AgentResult).filter_by(run_id=run_id).all()
        score = s.query(Score).filter_by(run_id=run_id).first()
        report = s.query(Report).filter_by(run_id=run_id).first()
        done = sum(1 for r in results if r.status in ("completed", "failed"))
        total = max(len(results), len(PHASE0_AGENTS))
        return {
            "run_id": run_id,
            "status": run.status,
            "stage": run.stage,
            "progress": round(0.25 + 0.6 * (done / total), 2) if total else 0.1,
            "warnings": run.warnings or [],
            "error": run.error,
            "cost_usd": round(run.cost_usd or 0, 4),
            "provider": run.provider_bundle,
            "document": {
                "filename": doc.filename, "pages": doc.page_count,
                "language": doc.language, "doc_type": doc.doc_type,
                "visual_mode": doc.visual_mode,
            } if doc else None,
            "agents": [
                {"key": r.agent_key, "title": DIMENSIONS.get(r.agent_key, r.agent_key),
                 "status": r.status, "score": r.score, "coverage": r.coverage,
                 "confidence": r.confidence, "payload": r.payload, "error": r.error}
                for r in results
            ],
            "score": {"overall": score.overall, "verdict": score.verdict,
                      "verdict_label": VERDICT_LABELS.get(score.verdict, ""),
                      "breakdown": score.breakdown} if score else None,
            "onepager": report.onepager if report else None,
            "report_html": f"/api/v1/runs/{run_id}/report.html" if report else None,
        }


def _set(run_id: str, **fields) -> None:
    with session_scope() as s:
        run = s.get(Run, run_id)
        for k, v in fields.items():
            setattr(run, k, v)
    emit(run_id, _snapshot(run_id))


def _add_cost(run_id: str, cost: float) -> None:
    with session_scope() as s:
        run = s.get(Run, run_id)
        run.cost_usd = (run.cost_usd or 0) + cost


def cross_validate(agent_payloads: dict[str, dict], onepager: dict) -> list[str]:
    """Проверки, которые не входят в компетенцию отдельного агента.

    Ловит класс ошибок из образца отчёта по Ably: красный флаг, противоречащий
    содержимому соседнего блока, и утверждения без источника.
    """
    issues: list[str] = []
    for key, payload in agent_payloads.items():
        for f in payload.get("findings", []):
            ev = f.get("evidence") or {}
            if f.get("source") == "deck" and not ev.get("page"):
                issues.append(
                    f"{DIMENSIONS.get(key, key)}: утверждение без ссылки на страницу — "
                    f"«{f.get('claim', '')[:60]}»")
            not_disclosed = [n.lower() for n in payload.get("not_disclosed", [])]
            claim = (f.get("claim") or "").lower()
            if f.get("type") == "red_flag" and any(n and n in claim for n in not_disclosed):
                issues.append(
                    f"{DIMENSIONS.get(key, key)}: красный флаг сформулирован как факт "
                    f"о компании, хотя данные просто не раскрыты в деке")
    if len(onepager.get("red_flags", [])) == 0:
        issues.append("Красные флаги не выделены ни одним агентом")
    return issues


async def _run_agent(run_id: str, key: str, fact_sheet: dict, pages: list[dict]) -> None:
    with session_scope() as s:
        res = s.query(AgentResult).filter_by(run_id=run_id, agent_key=key).first()
        res.status = "running"
    emit(run_id, _snapshot(run_id))

    t0 = time.time()
    try:
        async with _sem:
            out, cost = await run_dimension_agent(key, fact_sheet, pages)
        _add_cost(run_id, cost)
        with session_scope() as s:
            res = s.query(AgentResult).filter_by(run_id=run_id, agent_key=key).first()
            res.status = "completed"
            res.score, res.confidence, res.coverage = out.score, out.confidence, out.coverage
            res.payload = out.model_dump()
            res.latency_ms = int((time.time() - t0) * 1000)
    except Exception as e:
        with session_scope() as s:
            res = s.query(AgentResult).filter_by(run_id=run_id, agent_key=key).first()
            res.status = "failed"
            res.error = str(e)[:500]
    emit(run_id, _snapshot(run_id))


async def analyze(run_id: str) -> None:
    try:
        with session_scope() as s:
            run = s.get(Run, run_id)
            doc = s.get(Document, run.document_id)
            src, doc_id = doc.storage_key, doc.id

        # 1. Извлечение
        _set(run_id, status="running", stage="extracting")
        extracted = await asyncio.to_thread(extract_document, src, doc_id)
        with session_scope() as s:
            # страницы прошлого прогона этого же документа удаляем,
            # иначе при повторе они задвоятся
            s.query(Page).filter_by(document_id=doc_id).delete()
            doc = s.get(Document, doc_id)
            doc.page_count = len(extracted.pages)
            doc.visual_mode = extracted.visual_mode
            doc.pdf_key = extracted.pdf_path
            for p in extracted.pages:
                s.add(Page(document_id=doc_id, index=p.index, text=p.text,
                           image_key=p.image_path, ocr_used=int(p.ocr_used)))
            run = s.get(Run, run_id)
            run.warnings = extracted.warnings
        pages = [{"index": p.index, "text": p.text, "image_key": p.image_path}
                 for p in extracted.pages]
        if not pages:
            _set(run_id, status="failed", stage="extracting",
                 error="Из документа не удалось извлечь ни одной страницы")
            return

        # 2. Классификация документа
        _set(run_id, stage="classifying")
        cls, cost = await classify_document(pages)
        _add_cost(run_id, cost)
        with session_scope() as s:
            doc = s.get(Document, doc_id)
            doc.doc_type, doc.language = cls.doc_type, cls.language
        if cls.doc_type == "not_a_deck":
            _set(run_id, status="not_a_deck", stage="classifying",
                 error=f"Документ не является презентацией компании. {cls.reason}")
            return

        # 3. Структуризация
        _set(run_id, stage="structuring")
        structure, cost = await structurize(pages)
        _add_cost(run_id, cost)
        fact_sheet = structure.model_dump()
        with session_scope() as s:
            run = s.get(Run, run_id)
            run.fact_sheet = fact_sheet
            for p in structure.pages:
                page = s.query(Page).filter_by(document_id=doc_id, index=p.page).first()
                if page:
                    page.slide_type = p.slide_type

        if len(fact_sheet.get("facts", [])) < 5:
            _set(run_id, status="insufficient_input", stage="structuring",
                 error="В документе слишком мало извлекаемых фактов для анализа")
            return

        # 4. Агенты
        _set(run_id, stage="analyzing")
        with session_scope() as s:
            for key in PHASE0_AGENTS:
                s.add(AgentResult(run_id=run_id, agent_key=key, status="queued"))
        await asyncio.gather(*[_run_agent(run_id, k, fact_sheet, pages)
                               for k in PHASE0_AGENTS])

        # 5. Скор и вердикт
        _set(run_id, stage="aggregating")
        with session_scope() as s:
            results = s.query(AgentResult).filter_by(run_id=run_id).all()
            payloads = {r.agent_key: (r.payload or {}) for r in results if r.status == "completed"}
            scores = {r.agent_key: r.score for r in results if r.status == "completed"}
            coverages = [r.coverage for r in results if r.coverage is not None]
            doc_type = s.get(Document, doc_id).doc_type

        if not scores:
            _set(run_id, status="failed", stage="analyzing",
                 error="Ни один агент не завершился успешно")
            return

        overall = compute_overall(scores)
        avg_cov = sum(coverages) / len(coverages) if coverages else 0.0
        has_high_rf = any(f.get("type") == "red_flag" and f.get("severity") == "high"
                          for p in payloads.values() for f in p.get("findings", []))
        verdict, note = decide_verdict(overall, avg_cov, doc_type, has_high_rf)

        with session_scope() as s:
            s.add(Score(run_id=run_id, overall=overall, verdict=verdict,
                        breakdown={"scores": scores, "coverage": round(avg_cov, 2),
                                   "note": note}))

        # 6. Одностраничник и кросс-валидация
        _set(run_id, stage="reporting")
        context = {
            "company": {"name": cls.company_name, "tagline": cls.tagline,
                        "stage": cls.stage, "sector": cls.sector},
            "overall": overall, "verdict": VERDICT_LABELS[verdict], "verdict_note": note,
            "coverage": round(avg_cov, 2),
            "agents": {k: DIMENSIONS[k] for k in payloads},
            "analyses": payloads,
        }
        onepager, cost = await build_onepager(context)
        _add_cost(run_id, cost)
        op = onepager.model_dump()

        issues = cross_validate(payloads, op)
        html_key = render_report(run_id, op, overall, verdict, note, scores,
                                 cls.model_dump(), issues,
                                 extracted.visual_mode, doc_type)

        with session_scope() as s:
            s.add(Report(run_id=run_id, onepager=op, html_key=html_key))
            run = s.get(Run, run_id)
            run.warnings = (run.warnings or []) + issues
            run.finished_at = datetime.utcnow()

        failed = [r for r in results if r.status == "failed"]
        _set(run_id, status="partial" if failed else "completed", stage="done")

    except Exception as e:
        _set(run_id, status="failed", error=str(e)[:1000])
