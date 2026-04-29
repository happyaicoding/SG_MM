"""vectorbt 回測封裝。

接受策略訊號 (1/-1/0) + OHLC DataFrame，回傳標準化績效字典。

合約規格（預設值來自 config.yaml，可由 params 覆寫）：
    symbol      TX
    point_value 200   NT$/點
    commission  100   單邊（元）
    slippage    1     點
    init_cash   500_000
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from strategygen.backtest.metrics import calc_all

logger = logging.getLogger(__name__)

# 預設合約參數
_DEFAULT_PARAMS = {
    "point_value":   200,
    "commission":    100,
    "slippage":      1,
    "init_cash":     500_000,
    "max_position":  1,
}


def run_backtest(
    signals: pd.Series,
    df: pd.DataFrame,
    params: dict | None = None,
) -> dict:
    """執行全期回測，回傳標準化績效字典。

    Args:
        signals: 進出場訊號 Series（1=做多, -1=做空, 0=不動）
                 index 與 df 相同，已套用 delay=1（shift）
        df:      OHLC DataFrame，欄位 open/high/low/close
        params:  合約參數（覆寫預設值）

    Returns:
        dict 含：
            sharpe, max_drawdown, profit_factor, win_rate,
            total_trades, annual_return, avg_win, avg_loss,
            final_equity, equity（資金曲線 Series）
    """
    p = {**_DEFAULT_PARAMS, **(params or {})}

    try:
        import vectorbt as vbt
        return _run_vbt(signals, df, p)
    except ImportError:
        logger.warning("vectorbt 未安裝，使用簡易回測引擎")
        return _run_simple(signals, df, p)


# ── vectorbt 實作 ─────────────────────────────────────────────────────────

def _run_vbt(signals: pd.Series, df: pd.DataFrame, p: dict) -> dict:
    import vectorbt as vbt

    close = df["close"]
    long_entries  = signals == 1
    long_exits    = signals == -1
    short_entries = signals == -1
    short_exits   = signals == 1

    # 每點 NT$200，手續費以元換算成比例（相對 close * point_value）
    # vectorbt fees 是相對於 close 的比例
    fee_per_trade = p["commission"] + p["slippage"] * p["point_value"]

    pf = vbt.Portfolio.from_signals(
        close=close * p["point_value"],   # 換算為元
        entries=long_entries,
        exits=long_exits,
        short_entries=short_entries,
        short_exits=short_exits,
        init_cash=p["init_cash"],
        fees=0.0,               # 另外計算固定費用
        fixed_fees=fee_per_trade,
        sl_stop=None,
        tp_stop=None,
        accumulate=False,
    )

    trades = pf.trades.records_readable
    pnl_series = pd.Series(
        trades["PnL"].values if "PnL" in trades.columns else [],
        dtype=float,
    )
    equity = pf.value()

    result = calc_all(pnl_series, equity)
    result["final_equity"] = float(equity.iloc[-1])
    result["equity"]       = equity
    logger.info(
        "回測完成 | Sharpe=%.2f MaxDD=%.1f%% PF=%.2f Trades=%d",
        result["sharpe"], result["max_drawdown"] * 100,
        result["profit_factor"], result["total_trades"],
    )
    return result


# ── 簡易回測（無 vectorbt 時的備援）────────────────────────────────────────

def _run_simple(signals: pd.Series, df: pd.DataFrame, p: dict) -> dict:
    """純 pandas 事件驅動回測（備援用，效能較低）。"""
    close = df["close"]
    cash = float(p["init_cash"])
    position = 0      # 1=多, -1=空, 0=空手
    entry_price = 0.0
    pnl_list: list[float] = []
    equity_list: list[float] = []
    fee = p["commission"] + p["slippage"] * p["point_value"]

    for ts, sig in signals.items():
        price = float(close.loc[ts])

        # 平倉
        if position != 0 and sig != 0 and sig != position:
            trade_pnl = (price - entry_price) * position * p["point_value"] - fee * 2
            cash += trade_pnl
            pnl_list.append(trade_pnl)
            position = 0

        # 開倉
        if position == 0 and sig != 0:
            position = int(sig)
            entry_price = price
            cash -= fee

        unrealized = (price - entry_price) * position * p["point_value"] if position else 0.0
        equity_list.append(cash + unrealized)

    equity = pd.Series(equity_list, index=signals.index, dtype=float)
    pnl    = pd.Series(pnl_list, dtype=float)

    result = calc_all(pnl, equity)
    result["final_equity"] = float(equity.iloc[-1]) if len(equity) else float(p["init_cash"])
    result["equity"]       = equity
    return result
