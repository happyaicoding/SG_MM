"""AI 自動生成策略：MACD_MA_Trend。
由 StrategyGenerator 產生，請勿手動修改。
"""
# （完整策略程式碼，從 from __future__ import annotations 開始）
from __future__ import annotations
import pandas as pd
import pandas_ta as ta
from src.core.backtest.base_strategy import BaseStrategy


class MACD_MA_Trend(BaseStrategy):
    """MACD 交叉與均線排列確認趨勢策略"""

    NAME     = "MACD_MA_Trend"
    CATEGORY = "trend"
    PARAMS   = {
        "macd_fast":   12,   # MACD 快線週期
        "macd_slow":   26,   # MACD 慢線週期
        "macd_signal": 9,    # MACD 訊號線週期
        "ma_short":    20,   # 短期均線週期
        "ma_long":     60,   # 長期均線週期
    }

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        # 計算 MACD
        macd = ta.macd(
            df["close"],
            fast=self.params["macd_fast"],
            slow=self.params["macd_slow"],
            signal=self.params["macd_signal"]
        )
        macd_line   = macd[f"MACD_{self.params['macd_fast']}_{self.params['macd_slow']}_{self.params['macd_signal']}"]
        signal_line = macd[f"MACDs_{self.params['macd_fast']}_{self.params['macd_slow']}_{self.params['macd_signal']}"]

        # 計算均線
        ma_short = df["close"].rolling(self.params["ma_short"]).mean()
        ma_long  = df["close"].rolling(self.params["ma_long"]).mean()

        # MACD 交叉偵測
        macd_bullish_cross = (macd_line > signal_line) & macd_line.shift(1).le(signal_line.shift(1))
        macd_bearish_cross = (macd_line < signal_line) & macd_line.shift(1).ge(signal_line.shift(1))

        # 均線多頭排列：短期 > 長期
        ma_bullish = ma_short > ma_long
        # 均線空頭排列：短期 < 長期
        ma_bearish = ma_short < ma_long

        # 結合 MACD 交叉與均線排列
        long_signal  = macd_bullish_cross & ma_bullish
        short_signal = macd_bearish_cross & ma_bearish

        # 產生訊號
        signals = pd.Series(0, index=df.index)
        signals[long_signal]  = 1
        signals[short_signal] = -1

        # 當趨勢反轉時，平倉並反向進場
        signals = signals.replace(0, pd.NA).ffill().fillna(0)
        signals[long_signal]  = 1
        signals[short_signal] = -1

        return signals.shift(1).fillna(0).astype(int)

    def metadata(self) -> dict:
        return {
            "name":        self.NAME,
            "category":    self.CATEGORY,
            "params":      self.params,
            "description": "MACD 交叉搭配均線排列確認，多空雙向進場，日內短線趨勢策略",
        }

    def validate_params(self, params: dict) -> bool:
        return (
            params["macd_fast"]   < params["macd_slow"] and
            params["ma_short"]    < params["ma_long"]   and
            all(v > 0 for v in params.values())
        )