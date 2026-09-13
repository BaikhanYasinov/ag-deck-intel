"""Маршрутизация вызовов: какая роль на каком провайдере и какой модели."""
from __future__ import annotations

import json
from typing import Type, TypeVar

from pydantic import BaseModel, ValidationError

from ..config import VISION_MODE, role_model
from .blocks import extract_json, text_block
from .providers import get_provider

T = TypeVar("T", bound=BaseModel)


def resolve(role: str):
    """Возвращает (провайдер, имя модели) для роли."""
    provider_name, model = role_model(role)
    return get_provider(provider_name), model


def vision_mode_for(role: str) -> str:
    """batch — все изображения в одном запросе, per_page — по странице за раз.

    В режиме auto решение принимается по провайдеру: у локальных рантаймов
    многокартиночные запросы поддерживаются не всеми моделями, поэтому там
    безопаснее постранично.
    """
    if VISION_MODE in ("batch", "per_page"):
        return VISION_MODE
    provider, _ = resolve(role)
    return "batch" if provider.supports_multi_image else "per_page"


async def json_call(role: str, system: str, blocks: list[dict], schema: Type[T],
                    max_tokens: int = 2000, attempts: int = 3) -> tuple[T, float]:
    """Вызывает модель и возвращает провалидированный объект и стоимость.

    Схема уходит провайдеру: там, где рантайм умеет ограничивать декодирование
    (Ollama, vLLM), ответ валиден по построению. Где не умеет — работает
    цикл починки с текстом ошибки. Молча резать текст нельзя.
    """
    provider, model = resolve(role)
    json_schema = schema.model_json_schema()

    sys_full = (
        f"{system}\n\n"
        "Отвечай ТОЛЬКО валидным JSON по схеме ниже. Без markdown, без пояснений.\n"
        "Соблюдай ограничения maxLength: если не помещаешься, сокращай "
        "формулировку, а не обрывай её.\n\n"
        f"JSON Schema:\n{json.dumps(json_schema, ensure_ascii=False)}"
    )

    current = blocks
    cost = 0.0
    last_err = ""

    for _ in range(attempts):
        res = await provider.complete(model, sys_full, current, json_schema, max_tokens)
        cost += res.cost_usd
        try:
            return schema.model_validate_json(extract_json(res.text)), cost
        except (ValidationError, json.JSONDecodeError) as e:
            last_err = str(e)[:1200]
            current = blocks + [text_block(
                "Предыдущий ответ не прошёл валидацию:\n" + last_err +
                "\nВерни исправленный JSON, уложись в лимиты длины."
            )]

    raise ValueError(
        f"[{provider.name}:{model}] невалидный JSON после {attempts} попыток: {last_err}")
