"""AI 自動生成策略：MACD_TrendConfirm。
由 StrategyGenerator 產生，請勿手動修改。
"""
from __future__ import annotations
import pandas as pd
import pandas_ta as ta
from src.core.backtest.base_strategy import BaseStrategy


class MACD_TrendConfirm(BaseStrategy):
    """MACD 交叉 + EMA 趨勢確認策略，多空皆做。"""

    NAME     = "MACD_TrendConfirm"
    CATEGORY = "trend"
    PARAMS   = {
        "fast":   12,   # MACD 快線週期
        "slow":   26,   # MACD 慢線週期
        "signal": 9,    # MACD Signal 週期
        "ema_len": 50,  # 確認用 EMA 週期
    }

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        F = self.params["fast"]
        S = self.params["slow"]
        G = self.params["signal"]
        E = self.params["ema_len"]

        # MACD: 柱子 = MACD - Signal
        macd = ta.macd(df["close"], fast=F, slow=S, signal=G)
        macd_line    = macd[f"MACD_{F}_{S}_{G}"]
        signal_line  = macd[f"MACDs_{F}_{S}_{G}"]
        hist         = macd[f"MACDh_{F}_{S}_{G}"]

        # 趨勢確認 EMA
        ema = ta.ema(df["close"], length=E)

        # --- 交叉偵測（避免 FutureWarning）---
        macd_above  = macd_line > signal_line
        macd_below  = macd_line < signal_line
        macd_cross_up   = macd_above & macd_above.shift(1).eq(False)   # 黃金交叉
        macd_cross_down = macd_below & macd_below.shift(1).eq(False)   # 死亡交叉

        # --- 訊號組合 ---
        # 做多：MACD 黃金交叉 且 收盤價站上 EMA（確認多頭）
        # 做空：MACD 死亡交叉 且 收盤價跌破 EMA（確認空頭）
        signals = pd.Series(0, index=df.index)
        signals[macd_cross_up  & (df["close"] > ema)] =  1
        signals[macd_cross_down & (df["close"] < ema)] = -1

        # 下一根開盤進場，shift(1) 避免前視偏差
        return signals.shift(1).fillna(0).astype(int)

    def metadata(self) -> dict:
        return {
            "name":        self.NAME,
            "category":    self.CATEGORY,
            "params":      self.params,
            "description": (
                "MACD 交叉 + EMA 趨勢確認策略："
                "MACD 黃金交叉且收盤價站上 EMA 50 做多；"
                "MACD 死亡交叉且收盤價跌破 EMA 50 做空，"
                "日盤與夜盤皆適用。"
            ),
        }

    def validate_params(self, params: dict) -> bool:
        return (
            params.get("fast", 12)   < params.get("slow", 26)
            and params.get("signal", 9) > 0
            and params.get("ema_len", 50) > 0
        )