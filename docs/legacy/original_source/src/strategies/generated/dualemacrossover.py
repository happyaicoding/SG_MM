"""AI 自動生成策略：DualEmaCrossover。
由 StrategyGenerator 產生，請勿手動修改。
"""
from __future__ import annotations

import pandas as pd
import pandas_ta as ta
from src.core.backtest.base_strategy import BaseStrategy


class DualEmaCrossoverStrategy(BaseStrategy):
    """雙均線交叉策略（EMA Fast/Slow）"""

    NAME     = "DualEmaCrossover"
    CATEGORY = "trend"
    PARAMS   = {
        "fast_length":  12,   # 快速均線週期
        "slow_length":  26,   # 慢速均線週期
        "atr_length":   14,   # ATR 計算週期
        "atr_threshold": 1.2, # ATR 倍數門檻（過濾假突破）
    }

    def __init__(self, params: dict | None = None):
        super().__init__(params)

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        fast_ema = ta.ema(df["close"], length=self.params["fast_length"])
        slow_ema = ta.ema(df["close"], length=self.params["slow_length"])
        atr      = ta.atr(df["high"], df["low"], df["close"], length=self.params["atr_length"])
        atr_ma   = atr.rolling(self.params["fast_length"]).mean()

        # 快速線上穿慢速線 → 做多
        fast_cross_up   = (fast_ema > slow_ema) & (fast_ema.shift(1) <= slow_ema.shift(1))
        # 快速線下穿慢速線 → 做空
        fast_cross_down = (fast_ema < slow_ema) & (fast_ema.shift(1) >= slow_ema.shift(1))

        # ATR 確認：波動度需高於閾值才進場
        vol_confirmed = atr > (self.params["atr_threshold"] * atr_ma)

        signals = pd.Series(0, index=df.index)
        signals[fast_cross_up  & vol_confirmed] =  1
        signals[fast_cross_down & vol_confirmed] = -1

        return signals.shift(1).fillna(0).astype(int)

    def metadata(self) -> dict:
        return {
            "name":        self.NAME,
            "category":    self.CATEGORY,
            "params":      self.params,
            "description": "雙均線交叉趨勢策略，搭配ATR波動度過濾假突破，支援多空雙向進場，適用於日內短線。",
        }

    def validate_params(self, params: dict) -> bool:
        if params["fast_length"] >= params["slow_length"]:
            return False
        if params["atr_length"] <= 0 or params["atr_threshold"] <= 0:
            return False
        return True