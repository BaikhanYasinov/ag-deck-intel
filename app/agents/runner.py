"""Запуск агентов поверх извлечённого дека.

Два режима работы со зрением. В batch все нужные страницы уходят в один
запрос: так дешевле и агент видит дек целиком. В per_page страницы идут
по одной, а суждение выносится отдельным текстовым вызовом поверх собранных
наблюдений. Второй режим нужен локальным рантаймам, где многокартиночные
запросы поддерживаются не всеми моделями.
"""
from __future__ import annotations

import json

from ..config import FUND_NAME, MAX_IMAGES_PER_CALL, MAX_VISION_PAGES
from ..llm import image_block, json_call, text_block, vision_mode_for
from ..rubric import DIMENSIONS, anchor_text, thesis_block
from ..schemas import (AgentOutput, DocClassification, Fact, OnePager, PageDigest,
                       PageLabel, PageObservation, StructureOutput)
from . import prompts


def _page_text(p: dict) -> str:
    return f"--- Страница {p['index']} ---\n{p.get('text') or '(без текста)'}"


def _blocks(pages: list[dict], images: int) -> list[dict]:
    """Текст всех переданных страниц плюс до `images` изображений."""
    out: list[dict] = []
    used = 0
    for p in pages:
        out.append(text_block(_page_text(p)))
        if used < images and p.get("image_key"):
            try:
                out.append(image_block(p["image_key"]))
                used += 1
            except Exception:
                pass
    return out


def _one_page_blocks(p: dict) -> list[dict]:
    out = [text_block(_page_text(p))]
    if p.get("image_key"):
        try:
            out.append(image_block(p["image_key"]))
        except Exception:
            pass
    return out


# --------------------------------------------------------------------------- #
# Классификация документа
# --------------------------------------------------------------------------- #
async def classify_document(pages: list[dict]) -> tuple[DocClassification, float]:
    head = pages[:6]
    limit = 1 if vision_mode_for("classifier") == "per_page" else min(6, MAX_IMAGES_PER_CALL)
    blocks = _blocks(head, images=limit)
    blocks.append(text_block(
        f"Всего страниц в документе: {len(pages)}. Классифицируй документ."))
    return await json_call("classifier", prompts.CLASSIFIER, blocks,
                           DocClassification, max_tokens=800)


# --------------------------------------------------------------------------- #
# Структуризация
# --------------------------------------------------------------------------- #
async def structurize(pages: list[dict]) -> tuple[StructureOutput, float]:
    if vision_mode_for("structurizer") == "per_page":
        return await _structurize_per_page(pages)

    blocks = _blocks(pages, images=min(MAX_VISION_PAGES, MAX_IMAGES_PER_CALL))
    blocks.append(text_block("Разметь страницы и извлеки факты."))
    return await json_call("structurizer", prompts.STRUCTURIZER, blocks,
                           StructureOutput, max_tokens=4000)


async def _structurize_per_page(pages: list[dict]) -> tuple[StructureOutput, float]:
    labels: list[PageLabel] = []
    facts: list[Fact] = []
    cost = 0.0

    for p in pages:
        blocks = _one_page_blocks(p)
        blocks.append(text_block(
            f"Определи тип слайда и извлеки факты со страницы {p['index']}."))
        try:
            digest, c = await json_call("structurizer", prompts.STRUCTURIZER, blocks,
                                        PageDigest, max_tokens=1200)
        except Exception:
            continue          # страница не разобралась, остальные не страдают
        cost += c
        labels.append(PageLabel(page=p["index"], slide_type=digest.slide_type))
        for f in digest.facts:
            f.page = p["index"]      # номер страницы знаем мы, не модель
            facts.append(f)

    return StructureOutput(pages=labels, facts=facts[:60]), cost


# --------------------------------------------------------------------------- #
# Доменные агенты
# --------------------------------------------------------------------------- #
_RELEVANT_TYPES = {
    "team": {"team", "cover", "contact"},
    "problem_solution": {"problem", "solution", "product", "cover"},
    "market": {"market", "competition", "business_model"},
    "product": {"product", "solution", "roadmap"},
    "business_model": {"business_model", "financials"},
    "traction": {"traction", "financials"},
    "ask_deal_fit": {"ask", "financials"},
}


def _relevant_pages(dimension: str, fact_sheet: dict, pages: list[dict]) -> list[dict]:
    """Страницы под измерение. Если разметка ничего не дала, отдаём начало
    дека: лишние токены дешевле пропущенного слайда."""
    wanted = _RELEVANT_TYPES.get(dimension, set())
    labels = {p["page"]: p["slide_type"] for p in fact_sheet.get("pages", [])}
    picked = [p for p in pages if labels.get(p["index"]) in wanted]
    return picked or pages[:12]


def _system_for(dimension: str) -> str:
    return prompts.DIMENSION_SYSTEM.format(
        fund=FUND_NAME,
        dimension_title=DIMENSIONS[dimension],
        thesis=thesis_block(),
        anchors=anchor_text(dimension),
        grounding=prompts.GROUNDING,
        language=prompts.OUTPUT_LANGUAGE,
    )


async def run_dimension_agent(dimension: str, fact_sheet: dict,
                              pages: list[dict]) -> tuple[AgentOutput, float]:
    system = _system_for(dimension)
    relevant = _relevant_pages(dimension, fact_sheet, pages)
    fact_text = ("Fact sheet, извлечённый из дека:\n" +
                 json.dumps(fact_sheet, ensure_ascii=False, indent=1)[:12000])

    if vision_mode_for(dimension) == "per_page":
        return await _dimension_per_page(dimension, system, fact_text, relevant)

    blocks = [text_block(fact_text)]
    blocks += _blocks(relevant, images=MAX_IMAGES_PER_CALL)
    blocks.append(text_block(f"Оцени измерение: {DIMENSIONS[dimension]}."))
    return await json_call(dimension, system, blocks, AgentOutput, max_tokens=2000)


async def _dimension_per_page(dimension: str, system: str, fact_text: str,
                              pages: list[dict]) -> tuple[AgentOutput, float]:
    """Сначала наблюдения по каждой странице, затем одно текстовое суждение.

    Разделение намеренное: смотреть и оценивать одновременно локальные модели
    умеют хуже, чем делать это в два шага.
    """
    cost = 0.0
    observations: list[str] = []

    for p in pages:
        blocks = _one_page_blocks(p)
        blocks.append(text_block(
            f"Отметь всё, что относится к измерению «{DIMENSIONS[dimension]}» "
            f"на странице {p['index']}. Оценку не выноси, только наблюдения. "
            f"Если страница нерелевантна, верни relevant=false."))
        try:
            obs, c = await json_call(dimension, system, blocks, PageObservation,
                                     max_tokens=600)
        except Exception:
            continue
        cost += c
        if obs.relevant and obs.notes:
            observations.append(f"Страница {p['index']}: " + "; ".join(obs.notes))

    blocks = [
        text_block(fact_text),
        text_block("Наблюдения по страницам:\n" + ("\n".join(observations) or "нет")),
        text_block(f"Вынеси оценку по измерению «{DIMENSIONS[dimension]}». "
                   f"Ссылайся только на номера страниц из наблюдений."),
    ]
    out, c = await json_call(dimension, system, blocks, AgentOutput, max_tokens=2000)
    return out, cost + c


# --------------------------------------------------------------------------- #
# Сборка одностраничника
# --------------------------------------------------------------------------- #
async def build_onepager(context: dict) -> tuple[OnePager, float]:
    blocks = [text_block(json.dumps(context, ensure_ascii=False, indent=1)[:20000]),
              text_block("Собери одностраничник.")]
    return await json_call("onepager", prompts.ONEPAGER, blocks, OnePager,
                           max_tokens=2500)
