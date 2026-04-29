"""tests/unit/data/test_trading_day.py — trading_day 邏輯單元測試（5 個邊界 case）。

Coverage:
  1. 連假前的週五夜盤
  2. 連假後的週一日盤
  3. 跨週末的夜盤
  4. 颱風假當日
  5. 一般日盤 / 夜盤
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from src.core.data.trading_day import (
    assign_trading_day,
    classify_session_type,
    is_day_session,
    is_night_session,
    is_non_trading,
    time,
)


class TestTradingDayBoundaryCases:
    """5 個邊界 case（依 user 要求）。"""

    def test_case1_friday_night_before_holiday(self) -> None:
        """連假前的週五夜盤（2015-02-13 週五 → 春節連假）。

        2015-02-13 15:00-23:59 的夜盤 → 下一個有日盤的日期是 2015-02-16（週一）。
        測試時使用完整 kbar_index（包含下一個日盤日的資料）。
        """
        # kbar_index 包含：週五 15:00（夜盤） + 週一 08:45（日盤）
        kbar_idx = pd.DatetimeIndex([
            "2015-02-13 15:00:00",
            "2015-02-16 08:45:00",
            "2015-02-16 08:46:00",
        ])

        ts = pd.Timestamp("2015-02-13 15:00:00")
        result = assign_trading_day(ts, kbar_idx)
        assert result == pd.Timestamp("2015-02-16").date()

        ts_late = pd.Timestamp("2015-02-13 22:00:00")
        result_late = assign_trading_day(ts_late, kbar_idx)
        assert result_late == pd.Timestamp("2015-02-16").date()

    def test_case2_monday_day_after_holiday(self) -> None:
        """連假後的週一日盤（2015-02-16 週一，春節後第一個交易日）。

        2015-02-16 08:45-13:45 的日盤 → 直接歸當日（2015-02-16）。
        """
        kbar_idx = pd.DatetimeIndex(["2015-02-16 08:45:00"])
        ts = pd.Timestamp("2015-02-16 08:46:00")
        result = assign_trading_day(ts, kbar_idx)
        assert result == pd.Timestamp("2015-02-16").date()

    def test_case3_night_across_weekend(self) -> None:
        """跨週末的夜盤（週五 15:00 → 週六 05:00）。

        2015-01-09（週五）15:00 的夜盤 → 下一個日盤日是 2015-01-12（週一）。
        """
        kbar_idx = pd.DatetimeIndex([
            "2015-01-09 15:00:00",
            "2015-01-12 08:45:00",
        ])

        ts_night = pd.Timestamp("2015-01-09 15:00:00")
        result = assign_trading_day(ts_night, kbar_idx)
        assert result == pd.Timestamp("2015-01-12").date()

        ts_late = pd.Timestamp("2015-01-10 01:00:00")  # 週六凌晨
        result_late = assign_trading_day(ts_late, kbar_idx)
        assert result_late == pd.Timestamp("2015-01-12").date()

    def test_case4_typhoon_holiday(self) -> None:
        """颱風假當日（2015-08-07 蘇迪勒颱風，當日停盤）。

        若 2015-08-07 當日無日盤 K 棒，00:00-05:00 的夜盤後段
        應歸到下一個有日盤的日期（2015-08-10，補上班日）。
        """
        kbar_idx = pd.DatetimeIndex([
            "2015-08-07 01:00:00",
            "2015-08-10 08:45:00",  # 補班日
        ])

        ts = pd.Timestamp("2015-08-07 01:00:00")
        result = assign_trading_day(ts, kbar_idx)
        assert result == pd.Timestamp("2015-08-10").date()

    def test_case5_normal_day_and_night_session(self) -> None:
        """一般日盤 / 夜盤（最常見的 case）。"""
        # 一般日盤：2015-01-05（週一）09:00 → 歸當日
        kbar_idx_day = pd.DatetimeIndex(["2015-01-05 08:45:00"])
        ts_day = pd.Timestamp("2015-01-05 09:00:00")
        result_day = assign_trading_day(ts_day, kbar_idx_day)
        assert result_day == pd.Timestamp("2015-01-05").date()

        # 一般夜盤前段：2015-01-05 15:00 → 下一個日盤日（2015-01-06）
        kbar_idx_night = pd.DatetimeIndex([
            "2015-01-05 15:00:00",
            "2015-01-06 08:45:00",  # 下一個日盤日
        ])
        ts_night = pd.Timestamp("2015-01-05 15:00:00")
        result_night = assign_trading_day(ts_night, kbar_idx_night)
        assert result_night == pd.Timestamp("2015-01-06").date()

        # 一般夜盤後段：2015-01-06 01:00 → 該日有日盤（2015-01-06）→ 歸當日
        ts_late = pd.Timestamp("2015-01-06 01:00:00")
        result_late = assign_trading_day(ts_late, kbar_idx_night)
        assert result_late == pd.Timestamp("2015-01-06").date()


class TestSessionClassification:
    """session_type 分類正確性測試。"""

    def test_day_session(self) -> None:
        assert classify_session_type(pd.Timestamp("2015-01-05 08:45:00")) == "day_session"
        assert classify_session_type(pd.Timestamp("2015-01-05 12:30:00")) == "day_session"
        assert classify_session_type(pd.Timestamp("2015-01-05 13:45:00")) == "day_session"

    def test_night_session(self) -> None:
        assert classify_session_type(pd.Timestamp("2015-01-05 15:00:00")) == "night_session"
        assert classify_session_type(pd.Timestamp("2015-01-05 22:00:00")) == "night_session"
        assert classify_session_type(pd.Timestamp("2015-01-06 02:00:00")) == "night_session"
        assert classify_session_type(pd.Timestamp("2015-01-06 05:00:00")) == "night_session"

    def test_non_trading(self) -> None:
        assert classify_session_type(pd.Timestamp("2015-01-05 13:46:00")) == "non_trading"
        assert classify_session_type(pd.Timestamp("2015-01-05 14:00:00")) == "non_trading"
        assert classify_session_type(pd.Timestamp("2015-01-06 06:00:00")) == "non_trading"
        assert classify_session_type(pd.Timestamp("2015-01-06 08:00:00")) == "non_trading"


class TestHelperFunctions:
    """is_day_session / is_night_session / is_non_trading 正確性。"""

    def test_day_bounds(self) -> None:
        assert is_day_session(time(8, 45)) is True
        assert is_day_session(time(8, 44)) is False
        assert is_day_session(time(13, 45)) is True
        assert is_day_session(time(13, 46)) is False

    def test_night_early(self) -> None:
        assert is_night_session(time(15, 0)) is True
        assert is_night_session(time(14, 59)) is False
        assert is_night_session(time(23, 59)) is True
        assert is_night_session(time(0, 0)) is True   # 夜盤後段（00:00-05:00）

    def test_night_late(self) -> None:
        assert is_night_session(time(0, 0)) is True
        assert is_night_session(time(5, 0)) is True
        assert is_night_session(time(5, 1)) is False

    def test_non_trading_midday(self) -> None:
        assert is_non_trading(time(13, 46)) is True
        assert is_non_trading(time(14, 0)) is True
        assert is_non_trading(time(14, 59)) is True

    def test_non_trading_early_morning(self) -> None:
        assert is_non_trading(time(5, 1)) is True
        assert is_non_trading(time(6, 0)) is True
        assert is_non_trading(time(8, 0)) is True
        assert is_non_trading(time(8, 44)) is True
