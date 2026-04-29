"""src/core/data/trading_day.py — 台指期「資料本身判定」trading_day 邏輯。

依 business_rules.md §2.3：
  - 日盤 08:45-13:45 → timestamp.date() 當日
  - 夜盤前段 15:00-23:59 → 找下一個有日盤 K 棒的日期
  - 夜盤後段 00:00-05:00 → 若當日有日盤 → 當日；否則找下一個交易日

不依賴節假日表，直接從資料本身判定。
"""
from __future__ import annotations

from datetime import date, time
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from pandas import DatetimeIndex

# ── 時間邊界常數 ─────────────────────────────────────────────────────────────

DAY_START = time(8, 45)
DAY_END = time(13, 45)
NIGHT_START = time(15, 0)
NIGHT_END = time(23, 59, 59)
NIGHT_LATE_START = time(0, 0)
NIGHT_LATE_END = time(5, 0)


def is_day_session(t: time) -> bool:
    """判定是否為日盤時段。"""
    return DAY_START <= t <= DAY_END


def is_night_session(t: time) -> bool:
    """判定是否為夜盤時段（含跨日部分）。"""
    return (NIGHT_START <= t <= NIGHT_END) or (NIGHT_LATE_START <= t <= NIGHT_LATE_END)


def is_non_trading(t: time) -> bool:
    """判定是否為非交易時段（13:45-15:00 / 05:00-08:45）。"""
    return not (is_day_session(t) or is_night_session(t))


def _has_day_session_data(d: date, kbar_index: DatetimeIndex) -> bool:
    """檢查 d 當日 08:45-13:45 是否有 K 棒。"""
    mask = (
        (kbar_index.date == d)
        & (kbar_index.time >= DAY_START)
        & (kbar_index.time <= DAY_END)
    )
    return mask.any()


def _find_next_trading_day(after_d: date, kbar_index: DatetimeIndex) -> date:
    """從 kbar_index 找出 after_d 之後第一個有日盤 K 棒的日期（不含夜盤）。

    Args:
        after_d: 參考日期（不含）
        kbar_index: 整份 1 分 K 的 DatetimeIndex

    Returns:
        第一個有日盤資料的交易日

    Raises:
        ValueError: 找不到後續交易日
    """
    candidates = kbar_index[
        (kbar_index.date > after_d)
        & (kbar_index.time >= DAY_START)
        & (kbar_index.time <= DAY_END)
    ]
    if candidates.empty:
        raise ValueError(f"找不到 {after_d} 以後的交易日")
    return candidates[0].date()


def assign_trading_day(ts: pd.Timestamp, kbar_index: DatetimeIndex) -> date | None:
    """依 timestamp 判斷所屬的台指期交易日（不依賴節假日表）。

    Args:
        ts: K 棒時間戳（含時區 awareness）
        kbar_index: 整份 1 分 K 的 DatetimeIndex（用於查下一個交易日）

    Returns:
        交易日 date，若為非交易時段則回傳 None
    """
    t = ts.time()
    d = ts.date()

    # 日盤時段 → 當日
    if is_day_session(t):
        return d

    # 夜盤前段（15:00-23:59）→ 找下一個有日盤的交易日
    if NIGHT_START <= t <= NIGHT_END:
        return _find_next_trading_day(d, kbar_index)

    # 夜盤後段（00:00-05:00）
    if NIGHT_LATE_START <= t <= NIGHT_LATE_END:
        if _has_day_session_data(d, kbar_index):
            return d
        else:
            return _find_next_trading_day(d, kbar_index)

    # 非交易時段
    return None


def classify_session_type(ts: pd.Timestamp) -> str:
    """根據 K 棒時間判定 session_type（回傳字串供 SQL 寫入）。

    Returns:
        "day_session" | "night_session" | "non_trading"
    """
    t = ts.time()
    if is_day_session(t):
        return "day_session"
    if is_night_session(t):
        return "night_session"
    return "non_trading"


# ── 向量化批次處理（用於 ETL）────────────────────────────────────────────────

def assign_trading_day_batch(
    df: pd.DataFrame, timestamp_col: str = "timestamp"
) -> pd.Series:
    """對整份 DataFrame 批次指派 trading_day（向量化，效能導向）。

    邏輯：
    1. 日盤時段 → 當日
    2. 夜盤前段 15:00-23:59 → 向量化：找下一個日盤日
    3. 夜盤後段 00:00-05:00 → 若該 date 有日盤 → 當日；否則 → 下一個日盤日

    Args:
        df: 含有 timestamp_col 的 DataFrame
        timestamp_col: 時間戳欄位名（default: "timestamp"）

    Returns:
        pd.Series[int]: 與 df 同長度的 trading_day date 列表（None 表示非交易時段）
    """
    ts_series = df[timestamp_col]
    dates = ts_series.dt.date
    times = ts_series.dt.time

    # 找出各時段
    is_day = times.apply(lambda t: is_day_session(t))
    is_night_early = times.apply(lambda t: NIGHT_START <= t <= NIGHT_END)
    is_night_late = times.apply(lambda t: NIGHT_LATE_START <= t <= NIGHT_LATE_END)

    result = pd.Series(index=df.index, dtype=object)

    # 1. 日盤 → 當日
    result[is_day] = dates[is_day]

    # 2. 夜盤前段（15:00-23:59）
    #    需要對每個唯一 date 找「下一個有日盤的日期」，用 groupby 加速
    night_early_dates = dates[is_night_early].unique()
    next_day_map: dict[date, date] = {}
    all_dates_in_index = pd.Index(ts_series.dt.date.unique()).sort_values()

    for d in night_early_dates:
        future_days = all_dates_in_index[all_dates_in_index > d]
        if future_days.empty:
            continue
        # 下一個有日盤資料的日期
        candidates = df.loc[
            (df[timestamp_col].dt.date.isin(future_days))
            & (times >= DAY_START)
            & (times <= DAY_END),
            timestamp_col,
        ].dt.date
        if not candidates.empty:
            next_day_map[d] = candidates.iloc[0]

    result[is_night_early] = dates[is_night_early].map(next_day_map)

    # 3. 夜盤後段（00:00-05:00）
    #    如果當日有日盤 K 棒 → 當日；否則 → 下一個日盤日
    night_late_dates = dates[is_night_late].unique()
    has_day_session_cache: dict[date, bool] = {}

    def get_next_day(d: date) -> date:
        future_days = all_dates_in_index[all_dates_in_index > d]
        if future_days.empty:
            return d
        candidates = df.loc[
            (df[timestamp_col].dt.date.isin(future_days))
            & (times >= DAY_START)
            & (times <= DAY_END),
            timestamp_col,
        ].dt.date
        if not candidates.empty:
            return candidates.iloc[0]
        return d

    for d in night_late_dates:
        if d not in has_day_session_cache:
            has_day_session_cache[d] = bool(
                df.loc[
                    (dates == d) & is_day, timestamp_col
                ].shape[0]
                > 0
            )
        if has_day_session_cache[d]:
            result[(is_night_late) & (dates == d)] = d
        else:
            result[(is_night_late) & (dates == d)] = get_next_day(d)

    return result
