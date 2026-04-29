"""績效指標計算 — Sharpe、MaxDD、Profit Factor、Win Rate、年化報酬。

所有函式接受「每筆交易損益」或「資金曲線」Series，回傳純數值。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# 台指期合約參數（與 config.yaml 對應，可由呼叫端覆寫）
POINT_VALUE = 200    # NT$/點
COMMISSION  = 100    # 單邊（元）
SLIPPAGE    = 1      # 點


def calc_all(
    pnl: pd.Series,
    equity: pd.Series,
    risk_free: float = 0.015,
) -> dict:
    """計算所有績效指標，回傳標準化字典。

    Args:
        pnl:    每筆交易損益 Series（元）
        equity: 資金曲線 Series（元）
        risk_free: 無風險年利率（預設 1.5%）
    """
    trades = pnl.dropna()
    wins  = trades[trades > 0]
    losses = trades[trades < 0]

    return {
        "sharpe":        sharpe_ratio(equity, risk_free),
        "max_drawdown":  max_drawdown(equity),
        "profit_factor": profit_factor(trades),
        "win_rate":      len(wins) / len(trades) if len(trades) else 0.0,
        "total_trades":  len(trades),
        "annual_return": annual_return(equity),
        "avg_win":       float(wins.mean())   if len(wins)   else 0.0,
        "avg_loss":      float(losses.mean()) if len(losses) else 0.0,
    }


def sharpe_ratio(equity: pd.Series, risk_free: float = 0.015) -> float:
    """年化 Sharpe Ratio。

    Args:
        equity:    資金曲線（每分鐘 or 每日淨值）
        risk_free: 無風險年利率
    """
    returns = equity.pct_change().dropna()
    if returns.std() == 0:
        return 0.0
    # 依資料頻率推算年化倍數
    freq = _infer_annual_factor(equity)
    excess = returns - (risk_free / freq)
    return float((excess.mean() / excess.std()) * np.sqrt(freq))


def max_drawdown(equity: pd.Series) -> float:
    """最大回撤（相對值，正數表示回撤幅度）。"""
    peak = equity.cummax()
    drawdown = (equity - peak) / peak
    return float(abs(drawdown.min()))


def profit_factor(pnl: pd.Series) -> float:
    """Profit Factor = 總獲利 / 總虧損。"""
    gross_profit = pnl[pnl > 0].sum()
    gross_loss   = abs(pnl[pnl < 0].sum())
    if gross_loss == 0:
        return float("inf") if gross_profit > 0 else 0.0
    return float(gross_profit / gross_loss)


def annual_return(equity: pd.Series) -> float:
    """年化報酬率（CAGR）。"""
    if len(equity) < 2 or equity.iloc[0] == 0:
        return 0.0
    years = _infer_years(equity)
    if years <= 0:
        return 0.0
    total_return = equity.iloc[-1] / equity.iloc[0]
    return float(total_return ** (1 / years) - 1)


# ── helpers ──────────────────────────────────────────────────────────────────

def _infer_annual_factor(equity: pd.Series) -> float:
    """依 index 頻率推算每年交易區間數（1min→約 98,280、1d→約 245）。"""
    if not isinstance(equity.index, pd.DatetimeIndex) or len(equity) < 2:
        return 245.0   # 預設日頻
    delta = (equity.index[-1] - equity.index[0]).total_seconds() / (len(equity) - 1)
    seconds_per_year = 365.25 * 24 * 3600
    return seconds_per_year / delta


def _infer_years(equity: pd.Series) -> float:
    if isinstance(equity.index, pd.DatetimeIndex) and len(equity) >= 2:
        return (equity.index[-1] - equity.index[0]).days / 365.25
    return 1.0
