"""AI 自動生成策略：MACD_RSI_Trend。
由 StrategyGenerator 產生，請勿手動修改。
"""
from __future__ import annotations
import pandas as pd
import pandas_ta as ta
from src.core.backtest.base_strategy import BaseStrategy

class MACD_RSI_Trend(BaseStrategy):
    NAME = "MACD_RSI_Trend"
    CATEGORY = "trend"
    PARAMS = {
        "ema_len": 20,       # 趨勢過濾 EMA 長度
        "macd_fast": 12,     # MACD 快線長度
        "macd_slow": 26,     # MACD 慢線長度
        "macd_sig": 9,       # MACD 訊號線長度
        "rsi_len": 14,       # RSI 長度
    }

    def validate_params(self, params: dict) -> bool:
        """回傳 True 表示參數合法。"""
        required_keys = ["ema_len", "macd_fast", "macd_slow", "macd_sig", "rsi_len"]
        if not all(key in params for key in required_keys):
            return False
        if not all(isinstance(params[k], int) and params[k] > 0 for k in required_keys):
            return False
        if params["macd_fast"] >= params["macd_slow"]:
            return False
        return True

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        """回傳 pd.Series，index 與 df 相同。值域：1=做多, -1=做空, 0=不動。
        最後必須 shift(1) 避免 lookahead bias（表示下一根開盤進場）。
        """
        # 計算指標
        ema_trend = ta.ema(df["close"], length=self.params["ema_len"])
        macd_df = ta.macd(
            df["close"],
            fast=self.params["macd_fast"],
            slow=self.params["macd_slow"],
            signal=self.params["macd_sig"]
        )
        rsi = ta.rsi(df["close"], length=self.params["rsi_len"])

        # 提取 MACD 線與訊號線 (pandas_ta 預設欄位順序：MACD, Signal, Hist)
        macd_line = macd_df.iloc[:, 0]
        signal_line = macd_df.iloc[:, 1]

        # 趨勢過濾：價格在 EMA 之上為多頭環境，之下為空頭環境
        trend_up = df["close"] > ema_trend
        trend_down = df["close"] < ema_trend

        # MACD 交叉偵測 (避免使用 fillna)
        macd_cross_up = (macd_line > signal_line) & (macd_line.shift(1) <= signal_line.shift(1))
        macd_cross_down = (macd_line < signal_line) & (macd_line.shift(1) >= signal_line.shift(1))

        # RSI 動能確認
        rsi_momentum_up = rsi > 50
        rsi_momentum_down = rsi < 50

        # 進場條件
        long_condition = trend_up & macd_cross_up & rsi_momentum_up
        short_condition = trend_down & macd_cross_down & rsi_momentum_down

        # 生成訊號
        signals = pd.Series(0, index=df.index)
        signals[long_condition] = 1
        signals[short_condition] = -1

        # 避免前視偏差，訊號延後一根 K 棒
        return signals.shift(1).fillna(0).astype(int)

    def metadata(self) -> dict:
        return {
            "name": self.NAME,
            "category": self.CATEGORY,
            "params": self.params,
            "description": "結合 EMA 趨勢過濾、MACD 交叉與 RSI 動能確認的趨勢跟蹤策略。適用於台指期日盤趨勢行情，多頭時僅做多，空頭時僅做空。",
        }