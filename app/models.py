"""Модель данных. SQLite сейчас, Postgres позже: меняется только URL подключения."""
from __future__ import annotations

import uuid
from datetime import datetime
from contextlib import contextmanager

from sqlalchemy import (JSON, Column, DateTime, Float, ForeignKey, Integer,
                        String, Text, create_engine)
from sqlalchemy.orm import DeclarativeBase, Session, relationship, sessionmaker

from .config import DB_PATH

engine = create_engine(f"sqlite:///{DB_PATH}", future=True)
SessionLocal = sessionmaker(bind=engine, future=True, expire_on_commit=False)


def _uid() -> str:
    return uuid.uuid4().hex[:16]


class Base(DeclarativeBase):
    pass


class Document(Base):
    __tablename__ = "documents"
    id = Column(String, primary_key=True, default=_uid)
    filename = Column(String, nullable=False)
    ext = Column(String, nullable=False)
    size = Column(Integer)
    sha256 = Column(String, index=True)
    storage_key = Column(String)
    pdf_key = Column(String)
    page_count = Column(Integer, default=0)
    language = Column(String)
    doc_type = Column(String)
    visual_mode = Column(String, default="full")  # full | degraded
    created_at = Column(DateTime, default=datetime.utcnow)
    pages = relationship("Page", back_populates="document", cascade="all, delete-orphan")


class Page(Base):
    __tablename__ = "pages"
    id = Column(String, primary_key=True, default=_uid)
    document_id = Column(String, ForeignKey("documents.id"))
    index = Column(Integer)          # нумерация с 1, как её видит человек
    text = Column(Text)
    image_key = Column(String)
    ocr_used = Column(Integer, default=0)
    slide_type = Column(String)
    document = relationship("Document", back_populates="pages")


class Run(Base):
    __tablename__ = "runs"
    id = Column(String, primary_key=True, default=_uid)
    document_id = Column(String, ForeignKey("documents.id"))
    status = Column(String, default="queued")
    stage = Column(String, default="queued")
    rubric_id = Column(String, default="kg_early")
    provider_bundle = Column(String)   # чем именно считался этот прогон
    fact_sheet = Column(JSON)
    warnings = Column(JSON, default=list)
    error = Column(Text)
    cost_usd = Column(Float, default=0.0)
    started_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime)
    results = relationship("AgentResult", cascade="all, delete-orphan")


class AgentResult(Base):
    __tablename__ = "agent_results"
    id = Column(String, primary_key=True, default=_uid)
    run_id = Column(String, ForeignKey("runs.id"))
    agent_key = Column(String)
    status = Column(String, default="queued")
    score = Column(Integer)
    confidence = Column(Float)
    coverage = Column(Float)
    payload = Column(JSON)
    latency_ms = Column(Integer)
    error = Column(Text)


class Score(Base):
    __tablename__ = "scores"
    id = Column(String, primary_key=True, default=_uid)
    run_id = Column(String, ForeignKey("runs.id"))
    overall = Column(Integer)
    verdict = Column(String)
    breakdown = Column(JSON)


class Report(Base):
    __tablename__ = "reports"
    id = Column(String, primary_key=True, default=_uid)
    run_id = Column(String, ForeignKey("runs.id"))
    onepager = Column(JSON)
    html_key = Column(String)
    pdf_key = Column(String)
    generated_at = Column(DateTime, default=datetime.utcnow)


@contextmanager
def session_scope() -> Session:
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def _ensure_columns() -> None:
    """Мини-миграция для уже существующих баз.

    create_all не добавляет колонки в готовые таблицы, а Alembic ради одного
    поля разворачивать незачем: смотрим PRAGMA и дописываем недостающее.
    """
    from sqlalchemy import inspect, text
    inspector = inspect(engine)
    if "runs" not in inspector.get_table_names():
        return
    existing = {c["name"] for c in inspector.get_columns("runs")}
    if "provider_bundle" not in existing:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE runs ADD COLUMN provider_bundle VARCHAR"))


def init_db() -> None:
    Base.metadata.create_all(engine)
    _ensure_columns()
