"""Схемы вывода агентов.

Лимиты символов здесь не косметика: они не дают тексту вылезти за рамку
одностраничника. Валидация происходит до вёрстки, а не после.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

Source = Literal["deck", "web", "policy"]


class Evidence(BaseModel):
    page: Optional[int] = Field(None, description="Номер страницы дека, с 1")
    excerpt: str = Field("", max_length=300)
    url: Optional[str] = None


class Finding(BaseModel):
    type: Literal["strength", "concern", "red_flag"]
    claim: str = Field(..., max_length=180)
    source: Source = "deck"
    severity: Literal["high", "medium", "low"] = "medium"
    evidence: Evidence = Evidence()


class AgentOutput(BaseModel):
    score: int = Field(..., ge=1, le=5)
    confidence: float = Field(..., ge=0.0, le=1.0)
    coverage: float = Field(..., ge=0.0, le=1.0)
    summary: str = Field(..., max_length=200)
    findings: list[Finding] = Field(default_factory=list, max_length=6)
    not_disclosed: list[str] = Field(default_factory=list, max_length=6)
    questions_for_founder: list[str] = Field(default_factory=list, max_length=3)


class DocClassification(BaseModel):
    doc_type: Literal["fundraising_deck", "business_update", "product_overview",
                      "report", "not_a_deck"]
    language: Literal["ru", "en", "mixed", "other"]
    company_name: str = Field("", max_length=40)
    tagline: str = Field("", max_length=90)
    stage: str = Field("", max_length=30)
    sector: str = Field("", max_length=40)
    reason: str = Field("", max_length=200)


class Fact(BaseModel):
    key: str = Field(..., max_length=60)
    value: str = Field(..., max_length=160)
    page: int
    source: Source = "deck"


class PageLabel(BaseModel):
    page: int
    slide_type: str = Field(..., max_length=30)


class StructureOutput(BaseModel):
    pages: list[PageLabel] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list, max_length=60)


class PageDigest(BaseModel):
    """Разбор одной страницы. Используется в постраничном режиме."""
    slide_type: str = Field(..., max_length=30)
    facts: list[Fact] = Field(default_factory=list, max_length=12)


class PageObservation(BaseModel):
    """Наблюдения агента по одной странице до вынесения оценки."""
    relevant: bool = True
    notes: list[str] = Field(default_factory=list, max_length=3)


class OnePager(BaseModel):
    """Поля одностраничника. Лимиты соответствуют приложению A из ТЗ."""
    company_name: str = Field(..., max_length=40)
    tagline: str = Field(..., max_length=90)
    meta: str = Field("", max_length=60)
    recommendation: str = Field(..., max_length=120)
    problem: str = Field("", max_length=320)
    solution: str = Field("", max_length=320)
    traction: list[str] = Field(default_factory=list, max_length=4)
    team: list[str] = Field(default_factory=list, max_length=3)
    ask: str = Field("", max_length=200)
    red_flags: list[str] = Field(default_factory=list, max_length=5)
    questions: list[str] = Field(default_factory=list, max_length=5)
