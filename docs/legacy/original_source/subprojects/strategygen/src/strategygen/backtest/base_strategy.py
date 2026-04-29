"""BaseStrategy — 所有策略必須繼承此類別。

介面規範（CLAUDE.md）：
    NAME   : 唯一識別碼（字串）
    PARAMS : 預設參數字典（最多 5 個可調參數）
    CATEGORY: 策略分類（如 "trend" / "mean_reversion" / "breakout"）

    generate_signals(df) → pd.Series  # 1=做多, -1=做空, 0=不動
    metadata()           → dict
    validate_params(params) → bool

注意：
    - generate_signals 必須設 delay=1（下一根開盤進場）避免 lookahead bias
    - Power Language 策略需附原始碼作為 docstring 或同目錄 .txt 檔
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class BaseStrategy(ABC):
    NAME: str = ""
    PARAMS: dict = {}
    CATEGORY: str = ""              # 小分類：trend / mean_reversion / opening / scalp / swing / pattern
    HOLDING_TYPE: str = "daytrade"  # 大分類：daytrade（當沖，當日平倉）/ swing（波段，跨日持有）
    TIMEFRAME: str = "1min"         # K 棒週期：1min / 5min / 15min / 60min / 1D

    def __init__(self, params: dict | None = None) -> None:
        self.params = {**self.PARAMS, **(params or {})}
        if not self.validate_params(self.params):
            raise ValueError(f"無效參數：{self.params}")

    @abstractmethod
    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        """根據 OHLC 資料產生進出場訊號。

        Args:
            df: DatetimeIndex，欄位 open/high/low/close（無成交量）

        Returns:
            pd.Series，index 與 df 相同，值為 1 / -1 / 0
            注意：需 shift(1) 模擬下一根開盤進場，避免 lookahead bias
        """

    @abstractmethod
    def metadata(self) -> dict:
        """回傳策略元資料，供 registry 索引與報表使用。

        Returns:
            dict，至少包含 name / category / params / description
        """

    def validate_params(self, params: dict) -> bool:
        """驗證參數合法性（可覆寫以加入自訂規則）。"""
        return True

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.NAME!r}, params={self.params})"
