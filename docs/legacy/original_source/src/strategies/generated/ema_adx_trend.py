"""AI 自動生成策略：EMA_ADX_Trend。
由 StrategyGenerator 產生，請勿手動修改。
"""
from __future__ import annotations
import pandas as pd
import pandas_ta as ta
from src.core.backtest.base_strategy import BaseStrategy


class EMA_ADX_TrendStrategy(BaseStrategy):
    """
    EMA 交叉 + ADX 濾網趨勢策略
    - 只在 ADX > 閾值時進場，確認趨勢足夠強
    - 多空皆做，15min 框架避免 1min 雜訊
    """

    NAME = "EMA_ADX_Trend"
    CATEGORY = "trend"
    TIMEFRAME = "15min"
    PARAMS = {
        "fast_len": 9,       # 快速 EMA 週期
        "slow_len": 21,      # 慢速 EMA 週期
        "adx_len": 14,       # ADX 計算週期
        "adx_thresh": 25,    # ADX 閾值（需足夠趨勢強度才進場）
    }

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        close = df["close"]

        # EMA 計算
        ema_fast = ta.ema(close, length=self.params["fast_len"])
        ema_slow = ta.ema(close, length=self.params["slow_len"])

        # ADX 計算（確認趨勢強度）
        adx_data = ta.adx(df["high"], df["low"], close, length=self.params["adx_len"])
        adx = adx_data[f"ADX_{self.params['adx_len']}"]

        # ── 穿越偵測（避免使用 fillna） ──
        # 快速線由下往上穿越慢速線 → 做多
        long_cross = (ema_fast > ema_slow) & (ema_fast.shift(1).le(ema_slow.shift(1)))
        # 快速線由上往下穿越慢速線 → 做空
        short_cross = (ema_fast < ema_slow) & (ema_fast.shift(1).ge(ema_slow.shift(1)))

        # ADX 確認趨勢強度
        strong_trend = adx > self.params["adx_thresh"]

        # ── 訊號生成 ──
        signals = pd.Series(0, index=df.index)
        signals[long_cross & strong_trend] = 1
        signals[short_cross & strong_trend] = -1

        return signals.shift(1).fillna(0).astype(int)

    def metadata(self) -> dict:
        return {
            "name": self.NAME,
            "category": self.CATEGORY,
            "params": self.params,
            "description": "EMA 交叉搭配 ADX 確認趨勢強度的 trend 策略，15min 多空皆做",
        }

    def validate_params(self, params: dict) -> bool:
        required = {"fast_len", "slow_len", "adx_len", "adx_thresh"}
        if not required.issubset(params.keys()):
            return False
        if params["fast_len"] >= params["slow_len"]:
            return False
        if params["adx_len"] < 2 or params["adx_thresh"] < 0:
            return False
        return True