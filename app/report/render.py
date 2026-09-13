"""Сборка одностраничника: Jinja2 -> HTML -> (опционально) PDF.

Вёрстка сеточная, с переносом текста. Фиксированных координат нет
намеренно: именно они в образце отчёта резали слова пополам.
"""
from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..config import ENRICHMENT_ENABLED, FUND_NAME, STORAGE_DIR
from ..rubric import DIMENSIONS, VERDICT_LABELS

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
env = Environment(loader=FileSystemLoader(TEMPLATES),
                  autoescape=select_autoescape(["html"]))

DOC_TYPE_LABELS = {
    "fundraising_deck": "Инвестиционный дек",
    "business_update": "Бизнес-апдейт",
    "product_overview": "Обзор продукта",
    "report": "Отчёт",
}


def render_report(run_id: str, onepager: dict, overall: int, verdict: str,
                  note: str, scores: dict, classification: dict,
                  issues: list[str], visual_mode: str, doc_type: str) -> str:
    html = env.get_template("onepager.html").render(
        fund=FUND_NAME,
        op=onepager,
        overall=overall,
        verdict=verdict,
        verdict_label=VERDICT_LABELS.get(verdict, verdict),
        note=note,
        scores=[{"key": k, "title": DIMENSIONS[k], "value": v * 20,
                 "raw": v} for k, v in scores.items()],
        classification=classification,
        doc_type_label=DOC_TYPE_LABELS.get(doc_type, doc_type),
        limited=doc_type != "fundraising_deck",
        issues=issues,
        visual_limited=visual_mode == "degraded",
        enrichment=ENRICHMENT_ENABLED,
    )
    out = STORAGE_DIR / "reports" / f"{run_id}.html"
    out.write_text(html, encoding="utf-8")
    return str(out)


def export_pdf(html_path: str) -> str | None:
    """Экспорт в PDF. Требует playwright; без него отчёт печатается из браузера."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    pdf_path = str(Path(html_path).with_suffix(".pdf"))
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(Path(html_path).as_uri())
        page.pdf(path=pdf_path, format="A4", print_background=True,
                 margin={"top": "10mm", "bottom": "10mm", "left": "10mm", "right": "10mm"})
        browser.close()
    return pdf_path
