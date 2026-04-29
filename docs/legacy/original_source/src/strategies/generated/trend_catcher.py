"""AI 自動生成策略：Trend_Catcher。
由 StrategyGenerator 產生，請勿手動修改。
"""
from __future__ import annotations
import pandas as pd
import pandas_ta as ta
from src.core.backtest.base_strategy import BaseStrategy


class Trend_Catcher(BaseStrategy):
    """
    EMA 三線合一趨勢策略
    - 快速 EMA 上穿慢速 EMA 形成多頭排列，確認上漲趨勢
    - 快速 EMA 下穿慢速 EMA 形成空頭排列，確認下跌趨勢
    - 結合 MACD 方向過濾，減少假突破
    - 日內持倉，收盤前平倉
    """

    NAME     = "Trend_Catcher"
    CATEGORY = "trend"
    PARAMS   = {
        "fast_ema":  9,   # 快速 EMA 週期
        "slow_ema":  21,  # 慢速 EMA 週期
        "macd_fast": 12,  # MACD 快線
        "macd_slow": 26,  # MACD 慢線
        "macd_signal": 9, # MACD 訊號線
    }

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        # --- EMA 計算 ---
        ema_fast = ta.ema(df["close"], length=self.params["fast_ema"])
        ema_slow = ta.ema(df["close"], length=self.params["slow_ema"])

        # --- MACD 計算 ---
        macd_df = ta.macd(
            df["close"],
            fast=self.params["macd_fast"],
            slow=self.params["macd_slow"],
            signal=self.params["macd_signal"]
        )
        _f = self.params['macd_fast']
        _s = self.params['macd_slow']
        _g = self.params['macd_signal']
        macd_line = macd_df[f"MACD_{_f}_{_s}_{_g}"]
        macd_hist = macd_df[f"MACDh_{_f}_{_s}_{_g}"]   # 柱狀體（MACDh），非訊號線（MACDs）

        # --- 進場條件 ---
        # 做多：快速 EMA 由下穿越慢速 EMA（黃金交叉）且 MACD 柱狀體為正
        long_cond = (
            (ema_fast > ema_slow) &
            (ema_fast.shift(1) <= ema_slow.shift(1)) &
            (macd_hist > 0)
        )

        # 做空：快速 EMA 由上穿越慢速 EMA（死亡交叉）且 MACD 柱狀體為負
        short_cond = (
            (ema_fast < ema_slow) &
            (ema_fast.shift(1) >= ema_slow.shift(1)) &
            (macd_hist < 0)
        )

        # --- 訊號組合 ---
        signals = pd.Series(0, index=df.index, dtype=int)
        signals[long_cond]  = 1
        signals[short_cond] = -1

        # shift(1) 避免前視偏差，下一根 K 開盤進場
        return signals.shift(1).fillna(0).astype(int)

    def metadata(self) -> dict:
        return {
            "name":        self.NAME,
            "category":    self.CATEGORY,
            "params":      self.params,
            "description": "結合 EMA 交叉與 MACD 確認的日內趨勢策略，多空皆可操作",
        }

    def validate_params(self, params: dict) -> bool:
        if params["fast_ema"] >= params["slow_ema"]:
            return False
        if params["macd_fast"] >= params["macd_slow"]:
            return False
        for key in ["fast_ema", "slow_ema", "macd_fast", "macd_slow", "macd_signal"]:
            if params[key] <= 0:
                return False
        return True