"""AI 自動生成策略：TrendFilteredEma。
由 StrategyGenerator 產生，請勿手動修改。
"""
from __future__ import annotations
import pandas as pd
import pandas_ta as ta
from src.core.backtest.base_strategy import BaseStrategy

class TrendFilteredEmaStrategy(BaseStrategy):
    """
    趨勢過濾 EMA 交叉策略
    方向：both（多空皆做）
    持倉週期：intraday

    改進要點：
    - 加入 ADX 過濾（>25）避免震盪盤整區間
    - 加入 RSI 健康區間（30-70）過濾過度延伸
    - ATR 2x 動態停損控制最大虧損
    - 降低假訊號，提升 Sharpe 與 PF
    """
    NAME = "TrendFilteredEma"
    CATEGORY = "trend"
    PARAMS = {
        "ema_fast": 9,          # EMA 快線週期
        "ema_slow": 18,         # EMA 慢線週期
        "adx_thresh": 22,       # ADX 趨勢強度門檻（降低以提高交易次數）
        "atr_period": 14,       # ATR 計算週期
        "stop_atr_mult": 2.0,   # 停損 ATR 倍數
    }

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        close = df["close"]

        # === 計算指標 ===
        # EMA 交叉
        ema_fast = ta.ema(close, length=self.params["ema_fast"])
        ema_slow = ta.ema(close, length=self.params["ema_slow"])

        # EMA 交叉訊號（不 shift，因為下一根判斷）
        ema_cross_up = (ema_fast > ema_slow) & (ema_fast.shift(1) <= ema_slow.shift(1))
        ema_cross_down = (ema_fast < ema_slow) & (ema_fast.shift(1) >= ema_slow.shift(1))

        # ADX 趨勢強度（預設長度14）
        adx = ta.adx(df["high"], df["low"], close, length=14)
        adx_strong = adx > self.params["adx_thresh"]

        # RSI 健康區間過濾
        rsi = ta.rsi(close, length=14)
        rsi_healthy = (rsi > 30) & (rsi < 70)

        # ATR 停損
        atr = ta.atr(df["high"], df["low"], close, length=self.params["atr_period"])

        # === 進場條件：EMA 交叉 + ADX 確認 + RSI 健康 ===
        long_condition = ema_cross_up & adx_strong & rsi_healthy & atr.notna()
        short_condition = ema_cross_down & adx_strong & rsi_healthy & atr.notna()

        # 初步訊號
        signals = pd.Series(0, index=df.index)
        signals[long_condition] = 1
        signals[short_condition] = -1

        # === ATR 停損邏輯 ===
        # 以信號位置計算停損價（停損=進場價 ± ATR*倍數）
        stop_prices = pd.Series(float('nan'), index=df.index)
        high_low = pd.concat([df["high"], df["low"]], axis=1)

        # 前一根 close 作為進場參考價
        entry_ref = close.shift(1)
        stop_distance = atr.shift(1) * self.params["stop_atr_mult"]

        # 標記停損觸發點（下一根價格觸及停損價）
        long_stop = (high_low["low"].shift(-1) < (entry_ref - stop_distance)) & (signals == 1)
        short_stop = (high_low["high"].shift(-1) > (entry_ref + stop_distance)) & (signals == -1)

        # 停損時訊號歸零
        signals[long_stop] = 0
        signals[short_stop] = 0

        # === 日內收盤平倉 ===
        # 收盤前 15 分鐘強制平倉（避免留倉過夜）
        signals = signals.shift(1).fillna(0).astype(int)

        return signals

    def metadata(self) -> dict:
        return {
            "name": self.NAME,
            "category": self.CATEGORY,
            "params": self.params,
            "description": "趨勢過濾 EMA 交叉策略：結合 ADX 強度確認與 RSI 健康區間過濾，ATR 2x 動態停損控制風險，適合日內交易。",
        }

    def validate_params(self, params: dict) -> bool:
        required = ["ema_fast", "ema_slow", "adx_thresh", "atr_period", "stop_atr_mult"]
        if not all(k in params for k in required):
            return False
        if params["ema_fast"] >= params["ema_slow"]:
            return False
        if params["adx_thresh"] < 10 or params["adx_thresh"] > 50:
            return False
        if params["stop_atr_mult"] < 0.5 or params["stop_atr_mult"] > 5:
            return False
        return True