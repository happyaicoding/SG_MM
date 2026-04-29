"""OHLC 資料重新採樣 — 將分K合成為任意週期 K 棒。

Usage:
    from src.core.data.resample import resample_ohlc

    df_60m  = resample_ohlc(df_1m, "60m")    # 60 分K
    df_45m  = resample_ohlc(df_1m, "45m")    # 自訂 45 分K
    df_90m  = resample_ohlc(df_1m, "90m")    # 自訂 90 分K
    df_3h   = resample_ohlc(df_1m, "3h")     # 3 小時K
    df_day  = resample_ohlc(df_1m, "D")      # 日K
"""
from __future__ import annotations

import logging
import re

import pandas as pd

logger = logging.getLogger(__name__)

# 固定別名（優先查找）
_ALIASES: dict[str, str] = {
    "1":   "1min",
    "1m":  "1min",
    "d":   "D",
    "day": "D",
    "1d":  "D",
    "w":   "W",
    "1w":  "W",
}


def _normalize_timeframe(timeframe: str) -> str:
    """將各種使用者輸入統一轉為 pandas resample 頻率字串。

    支援格式：
        "Nm"  → "Nmin"    e.g. "60m"→"60min", "45m"→"45min", "5m"→"5min"
        "Nh"  → "Nmin"    e.g. "1h"→"60min",  "2h"→"120min", "3h"→"180min"
        "N"   → "Nmin"    e.g. "60"→"60min"（純數字視為分鐘）
        "Nmin"→ 直接使用   e.g. "60min"→"60min"
        "D"   → "D"
        別名  → 查 _ALIASES
    """
    tf = timeframe.strip()
    lower = tf.lower()

    # 固定別名
    if lower in _ALIASES:
        return _ALIASES[lower]

    # Nm → Nmin（任意分鐘數）
    m = re.fullmatch(r"(\d+)m", lower)
    if m:
        return f"{m.group(1)}min"

    # Nh → N*60 min
    m = re.fullmatch(r"(\d+)h", lower)
    if m:
        minutes = int(m.group(1)) * 60
        return f"{minutes}min"

    # 純數字 → 視為分鐘
    m = re.fullmatch(r"(\d+)", tf)
    if m:
        n = int(m.group(1))
        return "1min" if n == 1 else f"{n}min"

    # 已是 pandas 格式（如 "60min", "D", "W"）直接使用
    return tf


def resample_ohlc(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """將 1 分K（或任意基礎週期）合成為指定週期 K 棒。

    Args:
        df:         DatetimeIndex DataFrame，欄位 open/high/low/close
        timeframe:  目標週期，支援任意分鐘數與小時數：
                    分鐘："5m" / "15m" / "30m" / "45m" / "60m" / "90m" / "120m" ...
                    小時："1h" / "2h" / "3h" / "4h" ...
                    純數字："60"（視為分鐘）
                    日K："D" / "day" / "1d"
                    已是 pandas 格式："60min" / "240min" 等

    Returns:
        重新採樣後的 DataFrame（DatetimeIndex，OHLC）
    """
    if df.empty:
        return df.copy()

    tf = _normalize_timeframe(timeframe)

    if tf == "1min":
        return df.copy()

    agg = {
        "open":  "first",
        "high":  "max",
        "low":   "min",
        "close": "last",
    }

    resampled = df.resample(tf, label="left", closed="left").agg(agg)
    resampled = resampled.dropna(subset=["open", "high", "low", "close"])

    before = len(df)
    after  = len(resampled)
    logger.info(
        "重新採樣：%s → %s  (%d → %d 根 bar)",
        "1min", tf, before, after,
    )
    return resampled
