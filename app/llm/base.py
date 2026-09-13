"""Контракт провайдера."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class Completion:
    text: str
    cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0


class Provider(Protocol):
    name: str
    supports_multi_image: bool

    async def complete(self, model: str, system: str, blocks: list[dict],
                       schema: dict | None, max_tokens: int) -> Completion:
        ...
