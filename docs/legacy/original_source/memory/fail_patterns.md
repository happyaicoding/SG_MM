# AISMART 策略生成失敗記錄

此檔案由系統自動維護，記錄歷次未通過 python_filter 的策略。
下次生成時會塞進 LLM system prompt 當反面教材。


## 2026-04-27 00:32 trend / both / 1min
- 策略：EMA_Channel_Trend
- 設計摘要：def generate_signals(self, df: pd.DataFrame) -> pd.Series: |         close = df["close"] |         # EMA 交叉 |         ema_fast = ta.ema(close, length=self.params["fast_len"]) |         ema_slow = ta.ema(close, length=self.params["slow_len"]) |         # 快速線由下往上穿越慢速線 → 做多 |         long_cond  = (ema_...
- 失敗：Sharpe -2.45 < 1.2、MaxDD 100.0% > 35%、PF 0.60 < 1.0

## 2026-04-27 00:58 trend / both / 15min
- 策略：EMA_Trend_Signal
- 設計摘要：def generate_signals(self, df: pd.DataFrame) -> pd.Series: |         close = df["close"] |         # ── EMA 計算（各自只算一次） ── |         ema_fast = ta.ema(close, length=self.params["fast_len"]) |         ema_slow = ta.ema(close, length=self.params["slow_len"]) |         # ── 趨勢方向 ── |         above_slow ...
- 失敗：Sharpe -0.77 < 1.2、MaxDD 60.6% > 35%、PF 0.87 < 1.0
