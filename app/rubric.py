"""Рубрика оценки: измерения, веса, якоря, вердикт.

Единственная шкала в продукте — 0..100. Внутри агенты оценивают по 1..5,
приведение к сотне происходит здесь и только здесь.
"""
from __future__ import annotations

from .config import (CHECK_MAX_USD, CHECK_MIN_USD, CHECK_TARGET_USD,
                     THESIS_GEO, THESIS_SECTOR, THESIS_STAGE)

DIMENSIONS = {
    "team": "Команда",
    "problem_solution": "Проблема и решение",
    "market": "Рынок",
    "product": "Продукт и защищённость",
    "business_model": "Бизнес-модель",
    "traction": "Трекшен",
    "ask_deal_fit": "Запрос и соответствие сделке",
}

# Фаза 0: работают три агента. Остальные подключаются в фазе 1,
# веса пересчитываются по фактически отработавшим измерениям.
PHASE0_AGENTS = ["team", "problem_solution", "market"]

RUBRICS = {
    "kg_early": {
        "name": "KG Early — ранняя стадия, Кыргызстан",
        "weights": {
            "team": 0.25,
            "problem_solution": 0.18,
            "market": 0.15,
            "product": 0.15,
            "business_model": 0.12,
            "traction": 0.10,
            "ask_deal_fit": 0.05,
        },
    },
    "global_growth": {
        "name": "Global Growth — калибровочный профиль",
        "weights": {
            "team": 0.12,
            "problem_solution": 0.12,
            "market": 0.15,
            "product": 0.15,
            "business_model": 0.16,
            "traction": 0.20,
            "ask_deal_fit": 0.10,
        },
    },
}

# Якоря. Заполнен только team — по формулировке инвестиционной команды.
# Остальные измерения пока идут на общем описании: это осознанный
# временный компромисс, он же главный источник разброса оценок.
ANCHORS = {
    "team": {
        5: "Основатели имеют прямой релевантный опыт в домене, компетенции "
           "взаимодополняющие, команда способна запустить продукт своими силами. "
           "Всё это подтверждено содержимым дека.",
        4: "Релевантный опыт есть, компетенции в основном закрыты, но одна "
           "ключевая роль вакантна или бэкграунд раскрыт неполно.",
        3: "Опыт есть, но команда неполная: для запуска продукта потребуются "
           "внешние подрядчики или найм ключевых специалистов.",
        2: "Опыт слабо связан с доменом, либо состав команды раскрыт настолько "
           "поверхностно, что оценить компетенции невозможно.",
        1: "Соло-основатель без релевантного бэкграунда, либо команда в деке "
           "не раскрыта вовсе.",
    },
}

GENERIC_ANCHOR = (
    "5 — сильно и подтверждено данными дека; 4 — убедительно, но с пробелами; "
    "3 — среднее, заявлено без доказательств; 2 — слабо или противоречиво; "
    "1 — отсутствует или неоценимо по деку. "
    "Оценивай строго по содержимому дека, не додумывай."
)


def anchor_text(dimension: str) -> str:
    if dimension in ANCHORS:
        return "\n".join(f"{k} — {v}" for k, v in sorted(ANCHORS[dimension].items(), reverse=True))
    return GENERIC_ANCHOR


def thesis_block() -> str:
    return (
        f"Стадия: {THESIS_STAGE}. Сектор: {THESIS_SECTOR}. География: {THESIS_GEO}. "
        f"Чек: от {CHECK_MIN_USD} до {CHECK_MAX_USD} USD, типичный {CHECK_TARGET_USD} USD."
    )


def compute_overall(scores: dict[str, int], rubric_id: str = "kg_early") -> int:
    """Взвешенная оценка по фактически отработавшим измерениям, шкала 0..100."""
    weights = RUBRICS[rubric_id]["weights"]
    used = {k: v for k, v in scores.items() if k in weights and v is not None}
    if not used:
        return 0
    total_w = sum(weights[k] for k in used)
    weighted = sum(used[k] * weights[k] for k in used) / total_w
    return round(weighted * 20)


def decide_verdict(overall: int, coverage: float, doc_type: str,
                   has_high_red_flag: bool) -> tuple[str, str]:
    """Возвращает (вердикт, пояснение).

    Два защитных правила идут раньше скора: система не выносит приговор
    компании, если на входе был не инвестиционный дек или в нём слишком
    мало данных.
    """
    if doc_type != "fundraising_deck":
        return "LIMITED", ("Документ не является инвестиционным деком, "
                           "вердикт по компании не выносится")
    if coverage < 0.5:
        return "INSUFFICIENT", ("В деке недостаточно данных для оценки: "
                                "требуются дополнительные материалы")
    if overall >= 70 and not has_high_red_flag:
        return "MEETING", "Соответствует критериям, рекомендуется встреча"
    if overall >= 50:
        return "WATCHLIST", "Наблюдать, вернуться при появлении новых данных"
    return "PASS", "Не соответствует критериям на текущем этапе"


VERDICT_LABELS = {
    "MEETING": "Взять встречу",
    "WATCHLIST": "В наблюдение",
    "PASS": "Отказ",
    "INSUFFICIENT": "Недостаточно данных",
    "LIMITED": "Ограниченная оценка",
}
