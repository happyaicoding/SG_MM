"""AI 自動生成策略：Trend_Breakout。
由 StrategyGenerator 產生，請勿手動修改。
"""
from __future__ import annotations
import pandas as pd
import pandas_ta as ta
from src.core.backtest.base_strategy import BaseStrategy


class TrendBreakoutStrategy(BaseStrategy):
    NAME     = "Trend_Breakout"
    CATEGORY = "trend"
    PARAMS   = {
        "ema_period":     50,   # EMA 趨勢判斷週期
        "breakout_window": 20,  # 突破窗口（近日高低點計算根數）
        "atr_multiplier": 2.0,  # ATR 止損倍數
        "close_before":   30,   # 收盤前 N 根強制平倉
    }

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        # 技術指標計算
        ema        = ta.ema(df["close"], length=self.params["ema_period"])
        window_high = df["high"].rolling(self.params["breakout_window"]).max()
        window_low  = df["low"].rolling(self.params["breakout_window"]).min()
        atr         = ta.atr(df["high"], df["low"], df["close"], length=14)

        # 進場條件：突破近日高低點 + 趨勢方向一致
        long_entry  = (df["close"] > ema) & (df["close"] > window_high)
        short_entry = (df["close"] < ema) & (df["close"] < window_low)

        # 動態 ATR 止損
        in_long  = long_entry.cumsum() - short_entry.cumsum() - (df["close"] < ema - self.params["atr_multiplier"] * atr).cumsum()
        in_short = short_entry.cumsum() - long_entry.cumsum() - (df["close"] > ema + self.params["atr_multiplier"] * atr).cumsum()

        stop_loss_long  = (df["close"] < ema - self.params["atr_multiplier"] * atr) & (in_long.shift(1) > 0)
        stop_loss_short = (df["close"] > ema + self.params["atr_multiplier"] * atr) & (in_short.shift(1) > 0)

        # 收盤前強制平倉
        close_bar = df.index[-1]
        force_close = pd.Series(False, index=df.index)
        if len(df) > self.params["close_before"]:
            force_close.iloc[-self.params["close_before"]:] = True

        # 訊號組合
        signals = pd.Series(0, index=df.index)
        signals[long_entry]  = 1
        signals[short_entry] = -1
        signals[stop_loss_long | stop_loss_short] = 0
        signals[force_close & (signals != 0)] = 0

        return signals.shift(1).fillna(0).astype(int)

    def metadata(self) -> dict:
        return {
            "name":        self.NAME,
            "category":    self.CATEGORY,
            "params":      self.params,
            "description": "結合 EMA 趨勢判斷與日內區間突破的趨勢策略，多頭時等待價格站上 EMA 並突破近日高，空頭時等待價格跌破 EMA 並跌破近日低，採用 ATR 動態止損控制風險，收盤前強制平倉確保日內結清。",
        }

    def validate_params(self, params: dict) -> bool:
        return (
            params.get("ema_period", 0) > 0
            and params.get("breakout_window", 0) > 0
            and params.get("atr_multiplier", 0) > 0
            and params.get("close_before", 0) > 0
        )