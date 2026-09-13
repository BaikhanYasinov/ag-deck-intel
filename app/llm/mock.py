"""Провайдер-заглушка: конвейер работает без единого вызова модели.

Нужен, чтобы проверять извлечение страниц, прогресс, вёрстку отчёта и базу
данных, не тратя токены и не имея ключа. Генерирует ответ прямо по JSON Schema,
поэтому валидация проходит всегда.

Все текстовые поля помечены словом ДЕМО. Отчёт, собранный на заглушке, должен
быть очевидно ненастоящим: инвестиционный меморандум, который выглядит
правдоподобно, но собран из выдуманных данных, опаснее отсутствия отчёта.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from .base import Completion

_LOREM = [
    "ДЕМО: заглушка вместо вывода модели",
    "ДЕМО: данные синтетические, выводы не имеют смысла",
    "ДЕМО: проверка конвейера без обращения к модели",
]


def _pick(schema: dict, defs: dict, depth: int = 0) -> Any:
    if "$ref" in schema:
        name = schema["$ref"].split("/")[-1]
        return _pick(defs.get(name, {}), defs, depth + 1)

    if "enum" in schema:
        return schema["enum"][0]

    if "anyOf" in schema:
        options = [o for o in schema["anyOf"] if o.get("type") != "null"]
        return _pick(options[0], defs, depth + 1) if options else None

    t = schema.get("type")

    if t == "object" or "properties" in schema:
        props = schema.get("properties", {})
        return {k: _pick(v, defs, depth + 1) for k, v in props.items()}

    if t == "array":
        # Не меньше трёх элементов: конвейер останавливается, если фактов
        # извлечено слишком мало, и демо-прогон не должен падать на этой проверке.
        n = min(schema.get("maxItems", 3), 6)
        n = max(n, min(3, schema.get("maxItems", 3)))
        item = schema.get("items", {"type": "string"})
        return [_pick(item, defs, depth + 1) for _ in range(n)]

    if t == "integer":
        lo, hi = schema.get("minimum", 1), schema.get("maximum", 5)
        return int((int(lo) + int(hi)) // 2)

    if t == "number":
        lo, hi = schema.get("minimum", 0.0), schema.get("maximum", 1.0)
        return round(float(lo) + (float(hi) - float(lo)) * 0.7, 2)

    if t == "boolean":
        return True

    text = _LOREM[depth % len(_LOREM)]
    limit = schema.get("maxLength")
    return text[:limit] if limit else text


class MockProvider:
    name = "mock"
    supports_multi_image = True

    async def complete(self, model, system, blocks, schema, max_tokens) -> Completion:
        await asyncio.sleep(0.4)          # чтобы прогресс в интерфейсе был виден
        if not schema:
            return Completion("pong", 0.0)
        defs = schema.get("$defs", {})
        return Completion(json.dumps(_pick(schema, defs), ensure_ascii=False), 0.0)
