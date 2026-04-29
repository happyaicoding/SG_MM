"""FastAPI 主程式 — AISMART Phase 1 基礎路由。

Phase 1 端點：
    POST /backtest/trigger              — 觸發 Python 初篩回測
    GET  /strategies                    — 查詢 DB 策略清單（分頁 + 狀態過濾）
    GET  /strategies/{id}               — 查詢策略詳情 + 最新回測結果
    GET  /strategies/available          — 列出 Registry 已註冊的策略
    POST /strategies/available/{name}/run — 直接以 Registry 策略名執行回測

Usage:
    python main.py web --port 8080
    uvicorn src.api.app:app --host 0.0.0.0 --port 8080 --reload
"""
from __future__ import annotations

import logging
from typing import Optional
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.db.init_db import get_db
from src.db.models import BacktestResult, Strategy

logger = logging.getLogger(__name__)

app = FastAPI(
    title="AISMART — AI 自動台指期策略開發系統",
    version="1.1.0",
    description="Phase 1：資料管線 + Python 初篩回測 API",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Pydantic Schemas ──────────────────────────────────────────────────────────

class BacktestTriggerRequest(BaseModel):
    strategy_id: str
    symbol: str = "TX"
    start: str | None = None
    end:   str | None = None


class RunByNameRequest(BaseModel):
    """直接以策略名稱 + 可選參數執行回測（不需先入庫）。"""
    symbol: str = "TX"
    start:  str | None = None
    end:    str | None = None
    params: dict | None = None


class BacktestResponse(BaseModel):
    strategy_name:  str
    result_id:      str
    symbol:         str
    start:          str
    end:            str
    sharpe_ratio:   float | None
    max_drawdown:   float | None
    profit_factor:  float | None
    total_trades:   int   | None
    win_rate:       float | None
    annual_return:  float | None
    final_equity:   float | None
    passed_filter:  bool
    filter_reasons: list[str]


class StrategyListItem(BaseModel):
    id: str
    name: str
    version: int
    strategy_type: str | None
    direction: str | None
    status: str
    created_at: str


class StrategyDetail(BaseModel):
    id: str
    name: str
    version: int
    strategy_type: str | None
    direction: str | None
    holding_period: str | None
    el_code: str | None
    prompt_summary: str | None
    risk_notes: str | None
    status: str
    created_at: str
    latest_backtest: dict | None = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _run_strategy_by_name(
    name: str,
    req: RunByNameRequest,
    strategy_db_id: str | None = None,
    db_session=None,
) -> BacktestResponse:
    """共用邏輯：依策略名稱執行回測。"""
    from src.core.backtest.runner import BacktestRunner
    from src.strategies.registry import get_strategy

    try:
        cls = get_strategy(name)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))

    strategy = cls(params=req.params)
    runner = BacktestRunner(db_session=db_session)

    try:
        result = runner.run(
            strategy,
            symbol=req.symbol,
            start=req.start,
            end=req.end,
            strategy_db_id=strategy_db_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("回測失敗：%s", e)
        raise HTTPException(status_code=500, detail=f"回測執行錯誤：{e}")

    return BacktestResponse(
        strategy_name  = result.strategy_name,
        result_id      = result.result_id,
        symbol         = result.symbol,
        start          = result.start,
        end            = result.end,
        sharpe_ratio   = result.sharpe_ratio,
        max_drawdown   = result.max_drawdown,
        profit_factor  = result.profit_factor,
        total_trades   = result.total_trades,
        win_rate       = result.win_rate,
        annual_return  = result.annual_return,
        final_equity   = result.final_equity,
        passed_filter  = result.passed_filter,
        filter_reasons = result.filter_reasons,
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "version": "1.1.0"}


@app.get("/strategies/available")
def list_available_strategies():
    """列出 Registry 中所有已註冊策略的 metadata。"""
    from src.strategies.registry import list_strategies
    return list_strategies()


@app.post("/strategies/available/{name}/run", response_model=BacktestResponse)
def run_strategy_by_name(
    name: str,
    req: RunByNameRequest,
    db: Session = Depends(get_db),
):
    """直接以策略名稱執行 Python 初篩回測（不需先在 DB 建立策略記錄）。

    範例：
        POST /strategies/available/MA_Cross/run
        {"symbol": "TX", "start": "2015-01-01", "end": "2021-12-31"}
    """
    return _run_strategy_by_name(name, req, db_session=db)


@app.post("/backtest/trigger", response_model=BacktestResponse)
def trigger_backtest(
    req: BacktestTriggerRequest,
    db: Session = Depends(get_db),
):
    """觸發 DB 中已存在策略的 Python 初篩回測。

    strategy_id 必須已存在於 strategies 表，且 name 需與 Registry 對應。
    """
    strategy_row = db.get(Strategy, req.strategy_id)
    if not strategy_row:
        raise HTTPException(status_code=404, detail=f"策略 {req.strategy_id!r} 不存在")

    run_req = RunByNameRequest(
        symbol=req.symbol,
        start=req.start,
        end=req.end,
    )
    return _run_strategy_by_name(
        strategy_row.name,
        run_req,
        strategy_db_id=req.strategy_id,
        db_session=db,
    )


@app.get("/strategies", response_model=list[StrategyListItem])
def list_strategies(
    page: int = 1,
    limit: int = 20,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """查詢 DB 策略清單（分頁 + 狀態過濾）。"""
    limit = min(limit, 100)
    query = db.query(Strategy)
    if status:
        query = query.filter(Strategy.status == status)
    rows = query.order_by(Strategy.created_at.desc()).offset((page - 1) * limit).limit(limit).all()

    return [
        StrategyListItem(
            id=r.id, name=r.name, version=r.version,
            strategy_type=r.strategy_type, direction=r.direction,
            status=r.status, created_at=str(r.created_at),
        )
        for r in rows
    ]


@app.get("/strategies/{strategy_id}", response_model=StrategyDetail)
def get_strategy_detail(strategy_id: str, db: Session = Depends(get_db)):
    """查詢策略詳情 + 最新回測結果。"""
    row = db.get(Strategy, strategy_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"策略 {strategy_id!r} 不存在")

    latest_bt = (
        db.query(BacktestResult)
        .filter(BacktestResult.strategy_id == strategy_id)
        .order_by(BacktestResult.created_at.desc())
        .first()
    )
    latest_dict = None
    if latest_bt:
        latest_dict = {
            "id": latest_bt.id, "engine": latest_bt.engine,
            "sharpe_ratio": latest_bt.sharpe_ratio,
            "max_drawdown": latest_bt.max_drawdown,
            "profit_factor": latest_bt.profit_factor,
            "total_trades": latest_bt.total_trades,
            "win_rate": latest_bt.win_rate,
            "overfitting_flag": latest_bt.overfitting_flag,
            "created_at": str(latest_bt.created_at),
        }

    return StrategyDetail(
        id=row.id, name=row.name, version=row.version,
        strategy_type=row.strategy_type, direction=row.direction,
        holding_period=row.holding_period, el_code=row.el_code,
        prompt_summary=row.prompt_summary, risk_notes=row.risk_notes,
        status=row.status, created_at=str(row.created_at),
        latest_backtest=latest_dict,
    )
