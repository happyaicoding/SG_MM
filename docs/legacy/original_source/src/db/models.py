"""SQLAlchemy ORM 模型 — AISMART 本地資料庫。

資料庫：data/aismart.db（SQLite）
表格：
    strategies       — 策略主表
    backtest_results — 回測結果（支援 python / multicharts 兩種引擎）
    alerts           — 健康監控警報紀錄
    url_knowledge    — Researcher URL 知識庫
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, relationship


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


# ── 策略主表 ──────────────────────────────────────────────────────────────────

class Strategy(Base):
    """策略主表。

    status 生命週期：
        draft → backtesting → reviewing → active / paused / retired
    """

    __tablename__ = "strategies"

    id             = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name           = Column(String(128), nullable=False)
    version        = Column(Integer, nullable=False, default=1)

    # 策略分類
    strategy_type  = Column(String(32))   # trend / mean_reversion / opening / intraday / swing
    direction      = Column(String(16))   # long / short / both
    holding_period = Column(String(16))   # scalp / intraday / swing / position

    # 程式碼與說明
    el_code        = Column(Text)
    prompt_summary = Column(Text)         # AI 生成的開發概要（150 字以內）
    risk_notes     = Column(Text)         # AI 生成的風險提示

    # 狀態
    status         = Column(
        String(16), nullable=False, default="draft"
    )  # draft / backtesting / reviewing / active / paused / retired

    created_at     = Column(DateTime, nullable=False, default=_utcnow)
    updated_at     = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)

    # 關聯
    backtest_results = relationship(
        "BacktestResult", back_populates="strategy", cascade="all, delete-orphan"
    )
    alerts = relationship(
        "Alert", back_populates="strategy", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Strategy id={self.id!r} name={self.name!r} status={self.status!r}>"


# ── 回測結果表 ────────────────────────────────────────────────────────────────

class BacktestResult(Base):
    """回測結果表。

    engine 欄位區分 Python 初篩與 MultiCharts 精測結果。
    """

    __tablename__ = "backtest_results"

    id              = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    strategy_id     = Column(String(36), ForeignKey("strategies.id"), nullable=False)
    engine          = Column(String(16), nullable=False)  # "python" / "multicharts"

    # 核心績效指標
    sharpe_ratio    = Column(Float)
    max_drawdown    = Column(Float)   # 小數（0.15 = 15%）
    profit_factor   = Column(Float)
    total_trades    = Column(Integer)
    win_rate        = Column(Float)   # 小數（0.55 = 55%）

    # Walk-Forward 驗證
    is_sharpe       = Column(Float)   # In-Sample Sharpe
    oos_sharpe      = Column(Float)   # Out-of-Sample Sharpe
    overfitting_flag = Column(Boolean, default=False)

    created_at      = Column(DateTime, nullable=False, default=_utcnow)

    # 關聯
    strategy = relationship("Strategy", back_populates="backtest_results")

    def __repr__(self) -> str:
        return (
            f"<BacktestResult engine={self.engine!r} "
            f"sharpe={self.sharpe_ratio} trades={self.total_trades}>"
        )


# ── 警報表 ────────────────────────────────────────────────────────────────────

class Alert(Base):
    """健康監控警報紀錄。

    level：
        yellow — 連虧 5 筆或回撤達上限 50%
        orange — 連虧 8 筆或回撤達上限 70%
        red    — 連虧 10 筆或回撤達上限 80%（強制暫停）
    """

    __tablename__ = "alerts"

    id           = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    strategy_id  = Column(String(36), ForeignKey("strategies.id"), nullable=False)
    level        = Column(String(8), nullable=False)  # "yellow" / "orange" / "red"
    message      = Column(Text)
    triggered_at = Column(DateTime, nullable=False, default=_utcnow)
    resolved_at  = Column(DateTime, nullable=True)

    # 關聯
    strategy = relationship("Strategy", back_populates="alerts")

    def __repr__(self) -> str:
        return f"<Alert level={self.level!r} strategy_id={self.strategy_id!r}>"


# ── URL 知識庫 ────────────────────────────────────────────────────────────────

class URLKnowledge(Base):
    """Researcher URL 知識庫 — 儲存使用者指定的參考網址與其擷取內容。

    使用方式：
        researcher.add_url("https://...")   → 抓取並儲存
        researcher.list_urls()              → 列出所有網址
        researcher.remove_url("https://...") → 刪除
        researcher.research(topic)          → 自動將已儲存內容注入 prompt
    """

    __tablename__ = "url_knowledge"

    id           = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    url          = Column(String(2048), nullable=False, unique=True)
    title        = Column(String(512))           # 網頁標題
    content      = Column(Text)                  # 擷取的純文字內容（最多 8000 字元）
    tags         = Column(String(512))           # 逗號分隔標籤，如 "台指期,MACD,趨勢"
    added_at     = Column(DateTime, nullable=False, default=_utcnow)
    last_fetched = Column(DateTime, nullable=False, default=_utcnow)
    fetch_ok     = Column(Boolean, default=True) # False 表示上次抓取失敗

    def __repr__(self) -> str:
        return f"<URLKnowledge url={self.url!r} title={self.title!r}>"
