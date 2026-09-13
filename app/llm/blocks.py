"""Нейтральное представление контента.

Канонический формат — блоки в стиле Anthropic. Провайдеры переводят их
в свой диалект. Так остальной код не знает, какой рантайм под ним.
"""
from __future__ import annotations

import base64
import re
from pathlib import Path


def text_block(text: str) -> dict:
    return {"type": "text", "text": text}


def image_block(path: str | Path) -> dict:
    p = Path(path)
    media = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
             "gif": "image/gif", "webp": "image/webp"}.get(p.suffix.lstrip(".").lower(),
                                                           "image/png")
    return {"type": "image",
            "source": {"type": "base64", "media_type": media,
                       "data": base64.b64encode(p.read_bytes()).decode()}}


def count_images(blocks: list[dict]) -> int:
    return sum(1 for b in blocks if b.get("type") == "image")


def extract_json(raw: str) -> str:
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    start, end = raw.find("{"), raw.rfind("}")
    return raw[start:end + 1] if start != -1 and end != -1 else raw
