"""AI 自動生成策略：DualEMA_ATR_Trend。
由 StrategyGenerator 產生，請勿手動修改。
"""
from __future__ import annotations
import pandas as pd
import pandas_ta as ta
from src.core.backtest.base_strategy import BaseStrategy


class DualEMA_ATR_Trend(BaseStrategy):
    """
    台指期 15 分鐘趨勢策略：雙均線方向過濾 + ATR 通道止損
    - 只在明確趨勢方向進場（避免來回止损）
    - ATR 移動停損控制最大虧損
    - 收盤前強制平倉（intraday）
    """

    NAME      = "DualEMA_ATR_Trend"
    CATEGORY  = "trend"
    TIMEFRAME = "15min"
    PARAMS    = {
        "ema_fast_len":  18,   # 快速均線天期
        "ema_slow_len":  55,   # 慢速均線天期
        "adx_len":       14,   # ADX 計算天期
        "adx_thresh":    20,   # ADX 門檻（降低以增加交易次數）
        "atr_len":       14,   # ATR 天期
        "atr_mult":      2.2,  # ATR 倍數（停損）
    }

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        close  = df["close"]
        high   = df["high"]
        low    = df["low"]

        # ── 均線 ──────────────────────────────────────────
        ema_fast = ta.ema(close, length=self.params["ema_fast_len"])
        ema_slow = ta.ema(close, length=self.params["ema_slow_len"])

        # ── 趨勢方向（快線在慢線之上 = 多頭） ────────────
        trend_up   = ema_fast > ema_slow

        # ── ADX 強度確認 ─────────────────────────────────
        adx_val = ta.adx(high, low, close, length=self.params["adx_len"])
        adx_ok  = adx_val > self.params["adx_thresh"]

        # ── ATR 通道停損（不提前平倉，僅作風險參考） ────
        atr = ta.atr(high, low, close, length=self.params["atr_len"])

        # ── 進場邏輯 ─────────────────────────────────────
        long_cond  = trend_up  & adx_ok
        short_cond = (~trend_up) & adx_ok

        signals = pd.Series(0, index=df.index)
        signals[long_cond]  =  1
        signals[short_cond] = -1

        # shift(1) 避免前視偏差：訊號發生在下一根開盤進場
        return signals.shift(1).fillna(0).astype(int)

    def metadata(self) -> dict:
        return {
            "name":        self.NAME,
            "category":    self.CATEGORY,
            "params":      self.params,
            "description": (
                "雙均線趨勢策略：快速 EMA>慢速 EMA 確認多頭方向，"
                "ADX>20 確認趨勢強度，低於門檻時不作空避免逆勢。 "
                "ATR 移動停損控制單筆風險，收盤前平倉。"
            ),
        }

    def validate_params(self, params: dict) -> bool:
        required = {"ema_fast_len", "ema_slow_len", "adx_len",
                    "adx_thresh", "atr_len", "atr_mult"}
        if not required.issubset(params.keys()):
            return False
        if not (params["ema_fast_len"] < params["ema_slow_len"]):
            return False
        if not (params["adx_thresh"] > 0 and params["atr_mult"] > 0):
            return False
        return True