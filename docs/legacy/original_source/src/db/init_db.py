"""資料庫初始化 — 建立 data/aismart.db（SQLite）與三張主表。

首次執行時建立資料表，已存在則跳過（idempotent）。
啟用 WAL 模式以支援 Celery 多 worker 並發讀取。

Usage:
    from src.db.init_db import init_db, get_session_factory

    engine = init_db()
    SessionLocal = get_session_factory(engine)

    with SessionLocal() as session:
        session.add(Strategy(name="MyStrategy"))
        session.commit()
"""
from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import Base

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = Path(__file__).parent.parent.parent / "data" / "aismart.db"
_DEFAULT_DB_URL  = f"sqlite:///{_DEFAULT_DB_PATH}"


def init_db(db_url: str | None = None) -> Engine:
    """建立 SQLite 資料庫與所有資料表（idempotent）。

    Args:
        db_url: SQLAlchemy 連線字串，預設 sqlite:///data/aismart.db

    Returns:
        已初始化的 SQLAlchemy Engine
    """
    url = db_url or _DEFAULT_DB_URL

    # 確保 data/ 目錄存在
    if url.startswith("sqlite:///"):
        db_file = Path(url[len("sqlite:///"):])
        db_file.parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(
        url,
        connect_args={"check_same_thread": False},  # SQLite 多執行緒
        echo=False,
    )

    # WAL 模式：允許多個 reader + 單一 writer 並發
    with engine.connect() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL"))
        conn.execute(text("PRAGMA foreign_keys=ON"))
        conn.commit()

    # 建立所有資料表（已存在則不重建）
    Base.metadata.create_all(engine)

    logger.info("資料庫初始化完成：%s", url)
    return engine


def get_session_factory(engine: Engine) -> sessionmaker[Session]:
    """回傳 sessionmaker，供 FastAPI dependency injection 使用。

    Args:
        engine: 已初始化的 Engine

    Returns:
        sessionmaker（autocommit=False, autoflush=False）
    """
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


# ── FastAPI dependency ────────────────────────────────────────────────────────

_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None


def get_engine() -> Engine:
    """取得全域 Engine（懶初始化）。"""
    global _engine
    if _engine is None:
        _engine = init_db()
    return _engine


def get_db():
    """FastAPI Dependency — yield SQLAlchemy Session，請求結束後自動關閉。

    Usage in FastAPI:
        @app.get("/strategies")
        def list_strategies(db: Session = Depends(get_db)):
            ...
    """
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = get_session_factory(get_engine())

    db = _SessionLocal()
    try:
        yield db
    finally:
        db.close()
