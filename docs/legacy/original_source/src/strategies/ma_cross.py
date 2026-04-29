"""MA 均線交叉策略 — 雙均線黃金交叉/死亡交叉。

邏輯：
    - 快線（短期 SMA）上穿慢線（長期 SMA）→ 做多訊號
    - 快線下穿慢線 → 做空訊號
    - 訊號 shift(1)：下一根開盤進場，避免 lookahead bias

參數：
    fast_period  : 快線週期（預設 20）
    slow_period  : 慢線週期（預設 60）
    use_close    : True=用收盤價計算 MA，False=用開盤價（預設 True）
"""
from __future__ import annotations

import pandas as pd

from src.core.backtest.base_strategy import BaseStrategy


class MACrossStrategy(BaseStrategy):
    NAME     = "MA_Cross"
    CATEGORY = "trend"
    PARAMS   = {
        "fast_period": 20,
        "slow_period": 60,
        "use_close":   True,
    }

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        """雙 SMA 交叉訊號。

        Returns:
            pd.Series（1=做多, -1=做空, 0=平倉/不動），shift(1) 後
        """
        price = df["close"] if self.params["use_close"] else df["open"]

        fast = price.rolling(self.params["fast_period"]).mean()
        slow = price.rolling(self.params["slow_period"]).mean()

        # 交叉偵測（使用 .eq() 避免 pandas 2.x boolean fillna FutureWarning）
        above = fast > slow                              # 快線在慢線上方
        cross_up   = above & above.shift(1).eq(False)   # 黃金交叉：前一根在下，現在在上
        cross_down = ~above & above.shift(1).eq(True)   # 死亡交叉：前一根在上，現在在下

        signals = pd.Series(0, index=df.index, dtype=int)
        signals[cross_up]   =  1   # 做多
        signals[cross_down] = -1   # 做空

        # shift(1)：下一根開盤才進場
        return signals.shift(1).fillna(0).astype(int)

    def metadata(self) -> dict:
        return {
            "name":        self.NAME,
            "category":    self.CATEGORY,
            "params":      self.params,
            "description": (
                f"雙 SMA 均線交叉策略。"
                f"快線={self.params['fast_period']}，慢線={self.params['slow_period']}。"
                f"黃金交叉做多，死亡交叉做空。"
            ),
        }

    def validate_params(self, params: dict) -> bool:
        fast = params.get("fast_period", 1)
        slow = params.get("slow_period", 1)
        return isinstance(fast, int) and isinstance(slow, int) and 0 < fast < slow
