"""RSI 超買超賣反轉策略。

邏輯：
    - RSI < oversold → 做多（超賣反彈）
    - RSI > overbought → 做空（超買回落）
    - 持倉期間 RSI 回到中線（50）→ 平倉轉為 0

參數：
    rsi_period  : RSI 計算週期（預設 14）
    oversold    : 超賣門檻（預設 30）
    overbought  : 超買門檻（預設 70）
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.core.backtest.base_strategy import BaseStrategy


class RSIReversalStrategy(BaseStrategy):
    NAME     = "RSI_Reversal"
    CATEGORY = "mean_reversion"
    PARAMS   = {
        "rsi_period": 14,
        "oversold":   30,
        "overbought": 70,
    }

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        """RSI 超買超賣訊號。

        Returns:
            pd.Series（1=做多, -1=做空, 0=不動/平倉），shift(1) 後
        """
        rsi = self._calc_rsi(df["close"], self.params["rsi_period"])

        oversold   = self.params["oversold"]
        overbought = self.params["overbought"]

        # 觸發訊號
        long_entry  = rsi < oversold                          # 超賣→做多
        short_entry = rsi > overbought                        # 超買→做空
        exit_signal = (rsi >= 50) & (rsi <= 50)              # 回中線平倉（細緻版在下方）

        # 用狀態機產生持倉訊號
        signals = pd.Series(0, index=df.index, dtype=int)
        position = 0

        for i in range(len(rsi)):
            r = rsi.iloc[i]
            if np.isnan(r):
                continue

            if position == 0:
                if r < oversold:
                    position = 1
                elif r > overbought:
                    position = -1
            elif position == 1:
                # 多頭持倉：RSI 回到 50 以上平倉
                if r >= 50:
                    position = 0
            elif position == -1:
                # 空頭持倉：RSI 回到 50 以下平倉
                if r <= 50:
                    position = 0

            signals.iloc[i] = position

        # 取差分得進出場訊號（0→1=做多, 0→-1=做空, 非0→0=平倉）
        diff = signals.diff().fillna(0)
        entry_signals = pd.Series(0, index=df.index, dtype=int)
        entry_signals[diff == 1]  =  1
        entry_signals[diff == -1] = -1
        entry_signals[diff.abs() > 1] = signals[diff.abs() > 1]  # 反手

        # shift(1)：下一根開盤進場
        return entry_signals.shift(1).fillna(0).astype(int)

    @staticmethod
    def _calc_rsi(close: pd.Series, period: int) -> pd.Series:
        """計算 RSI。"""
        delta = close.diff()
        gain  = delta.clip(lower=0)
        loss  = (-delta).clip(lower=0)
        avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
        avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
        rs  = avg_gain / avg_loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        return rsi

    def metadata(self) -> dict:
        return {
            "name":     self.NAME,
            "category": self.CATEGORY,
            "params":   self.params,
            "description": (
                f"RSI({self.params['rsi_period']}) 超買超賣反轉策略。"
                f"RSI < {self.params['oversold']} 做多，"
                f"RSI > {self.params['overbought']} 做空，"
                f"RSI 回 50 平倉。"
            ),
        }

    def validate_params(self, params: dict) -> bool:
        os = params.get("oversold", 30)
        ob = params.get("overbought", 70)
        rp = params.get("rsi_period", 14)
        return isinstance(rp, int) and rp > 1 and 0 < os < ob < 100
