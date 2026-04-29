"""資料品質驗證 — 缺口偵測、異常值標記、時段完整性檢查。

提供三項功能：
    1. 缺口偵測     — 找出交易時段內異常的時間跳空
    2. 異常值標記   — 標記 OHLC 邏輯錯誤、價格跳空過大的 bar
    3. 品質報告     — 彙整缺值率、異常比例、缺口分布

Usage:
    from src.core.data.validator import validate, DataQualityReport

    report = validate(df)
    print(report.summary())

    # 取出異常 bar
    bad = df[report.anomaly_mask]
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import NamedTuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ── 設定常數 ──────────────────────────────────────────────────────────────────

# 台指期交易時段（日盤 + 夜盤）
_DAY_START   = pd.Timestamp("08:45").time()
_DAY_END     = pd.Timestamp("13:45").time()   # exclusive
_NIGHT_START = pd.Timestamp("15:00").time()
_NIGHT_END   = pd.Timestamp("05:00").time()   # exclusive（次日）

# 缺口偵測：預期相鄰兩根 bar 的最大間距（分鐘）
_MAX_GAP_MINUTES = 5   # 正常允許 1 分鐘；5 分鐘以上視為缺口

# 異常值偵測：單根 bar 的允許最大價格跳幅（相對前一根 close 的比例）
_MAX_PRICE_JUMP_PCT = 0.05   # 5%（台指期一日漲跌限制約 10%）

# 除權跳空：close-to-open 超過此比例標記為疑似除權（台指期較少，但仍保留）
_EXDIV_JUMP_PCT = 0.03   # 3%


class Gap(NamedTuple):
    """一個時間缺口的描述。"""
    start: pd.Timestamp   # 缺口前最後一根 bar 的時間
    end: pd.Timestamp     # 缺口後第一根 bar 的時間
    gap_minutes: float    # 缺口長度（分鐘）
    session: str          # "day" / "night" / "cross-session"


@dataclass
class DataQualityReport:
    """資料品質報告。

    Attributes:
        total_bars:        原始 bar 總數
        valid_bars:        通過驗證的 bar 數
        null_count:        OHLC 任一欄為 NaN 的 bar 數
        ohlc_error_count:  high < low 或 open <= 0 的 bar 數
        price_jump_count:  單根價格跳幅 > _MAX_PRICE_JUMP_PCT 的 bar 數
        gaps:              List[Gap]，所有偵測到的時間缺口
        anomaly_mask:      boolean Series，True 表示該 bar 為異常
        date_range:        (min_dt, max_dt) tuple
    """
    total_bars:        int = 0
    valid_bars:        int = 0
    null_count:        int = 0
    ohlc_error_count:  int = 0
    price_jump_count:  int = 0
    gaps:              list[Gap] = field(default_factory=list)
    anomaly_mask:      pd.Series = field(default_factory=lambda: pd.Series(dtype=bool))
    date_range:        tuple[pd.Timestamp, pd.Timestamp] | None = None

    @property
    def anomaly_count(self) -> int:
        return int(self.anomaly_mask.sum())

    @property
    def null_rate(self) -> float:
        return self.null_count / self.total_bars if self.total_bars else 0.0

    @property
    def anomaly_rate(self) -> float:
        return self.anomaly_count / self.total_bars if self.total_bars else 0.0

    @property
    def gap_count(self) -> int:
        return len(self.gaps)

    def summary(self) -> str:
        """回傳人類可讀的品質報告字串。"""
        dr = (
            f"{self.date_range[0].date()} ~ {self.date_range[1].date()}"
            if self.date_range else "N/A"
        )
        lines = [
            "=" * 55,
            "  資料品質報告",
            "=" * 55,
            f"  資料區間    : {dr}",
            f"  總 bar 數   : {self.total_bars:>12,}",
            f"  有效 bar 數 : {self.valid_bars:>12,}",
            f"  NaN bar     : {self.null_count:>12,}  ({self.null_rate:.3%})",
            f"  OHLC 錯誤   : {self.ohlc_error_count:>12,}",
            f"  價格跳空    : {self.price_jump_count:>12,}",
            f"  時間缺口數  : {self.gap_count:>12,}",
            f"  異常 bar 率 : {self.anomaly_rate:>12.3%}",
            "-" * 55,
        ]
        if self.gaps:
            lines.append("  前 5 個時間缺口：")
            for g in self.gaps[:5]:
                lines.append(
                    f"    {g.start} → {g.end}  "
                    f"({g.gap_minutes:.0f} min, {g.session})"
                )
        lines.append("=" * 55)
        return "\n".join(lines)


# ── 主函式 ────────────────────────────────────────────────────────────────────

def validate(
    df: pd.DataFrame,
    max_gap_minutes: float = _MAX_GAP_MINUTES,
    max_price_jump_pct: float = _MAX_PRICE_JUMP_PCT,
) -> DataQualityReport:
    """執行完整的資料品質驗證。

    Args:
        df:                  DatetimeIndex DataFrame，欄位 open/high/low/close
        max_gap_minutes:     缺口判定閾值（分鐘），預設 5
        max_price_jump_pct:  價格跳幅異常閾值（相對比例），預設 0.05

    Returns:
        DataQualityReport
    """
    report = DataQualityReport()
    report.total_bars = len(df)

    if df.empty:
        logger.warning("validate: 傳入空 DataFrame")
        return report

    report.date_range = (df.index[0], df.index[-1])

    # ── 1. NaN 檢查 ───────────────────────────────────────────────
    null_mask = df[["open", "high", "low", "close"]].isnull().any(axis=1)
    report.null_count = int(null_mask.sum())

    # ── 2. OHLC 邏輯錯誤 ──────────────────────────────────────────
    ohlc_err_mask = (df["high"] < df["low"]) | (df["open"] <= 0) | (df["close"] <= 0)
    report.ohlc_error_count = int(ohlc_err_mask.sum())

    # ── 3. 價格跳空（單根 bar 相對前一根 close 的漲跌幅）──────────
    price_jump_mask = _detect_price_jumps(df, max_price_jump_pct)
    report.price_jump_count = int(price_jump_mask.sum())

    # ── 4. 彙整異常 mask ──────────────────────────────────────────
    report.anomaly_mask = null_mask | ohlc_err_mask | price_jump_mask
    report.valid_bars = int((~report.anomaly_mask).sum())

    # ── 5. 時間缺口偵測 ──────────────────────────────────────────
    report.gaps = detect_gaps(df, max_gap_minutes)

    logger.info(
        "驗證完成：%d 根 bar，異常 %d（%.2f%%），缺口 %d 個",
        report.total_bars, report.anomaly_count,
        report.anomaly_rate * 100, report.gap_count,
    )
    return report


# ── 缺口偵測 ──────────────────────────────────────────────────────────────────

def detect_gaps(
    df: pd.DataFrame,
    max_gap_minutes: float = _MAX_GAP_MINUTES,
) -> list[Gap]:
    """偵測 DatetimeIndex 中超過閾值的時間缺口。

    只在同一交易時段內的相鄰兩根 bar 之間判斷缺口；
    跨時段（日盤→夜盤、跨日）的正常間隔不計入。

    Args:
        df:               DatetimeIndex DataFrame
        max_gap_minutes:  超過此分鐘數才算缺口

    Returns:
        List[Gap]，依時間順序排列
    """
    if len(df) < 2:
        return []

    idx = df.index
    deltas = pd.Series(idx[1:] - idx[:-1], index=idx[1:])
    gap_minutes = deltas.dt.total_seconds() / 60

    gaps: list[Gap] = []

    for ts, minutes in gap_minutes.items():
        if minutes <= max_gap_minutes:
            continue

        prev_ts = idx[idx.get_loc(ts) - 1]
        session = _classify_gap_session(prev_ts, ts)

        # 跨時段的正常休市間隔（日盤結束→夜盤開始，夜盤結束→次日日盤）排除
        if session == "cross-session":
            continue

        gaps.append(Gap(
            start=prev_ts,
            end=ts,
            gap_minutes=float(minutes),
            session=session,
        ))

    logger.debug("偵測到 %d 個時間缺口（閾值 %d 分鐘）", len(gaps), max_gap_minutes)
    return gaps


def _classify_gap_session(prev_ts: pd.Timestamp, next_ts: pd.Timestamp) -> str:
    """判斷兩個時間點是否在同一連續交易時段，還是跨時段（正常休市）。

    規則：
        - 同日盤內（同一交易日，兩者均在 08:45~13:45）→ "day"
        - 同夜盤內（同一夜盤，日期差 ≤1 且兩者均在夜盤時段）→ "night"
        - 其他（日→隔日、日→夜、夜→日、週末）→ "cross-session"（正常休市，不計入缺口）
    """
    prev_t = prev_ts.time()
    next_t = next_ts.time()
    date_diff = (next_ts.date() - prev_ts.date()).days

    prev_day   = _DAY_START <= prev_t < _DAY_END
    next_day   = _DAY_START <= next_t < _DAY_END
    prev_night = prev_t >= _NIGHT_START or prev_t < _NIGHT_END
    next_night = next_t >= _NIGHT_START or next_t < _NIGHT_END

    # 同一交易日的日盤內缺口
    if prev_day and next_day and date_diff == 0:
        return "day"

    # 同一夜盤內缺口（跨日但不跨夜盤，例如 23:00 → 00:30 次日）
    if prev_night and next_night and date_diff <= 1:
        return "night"

    # 其他：跨交易日、日→夜、夜→日、週末休市 → 正常，不算缺口
    return "cross-session"


# ── 價格跳空偵測 ──────────────────────────────────────────────────────────────

def _detect_price_jumps(
    df: pd.DataFrame,
    threshold: float = _MAX_PRICE_JUMP_PCT,
) -> pd.Series:
    """計算每根 bar 相對前一根 close 的 open 漲跌幅，標記超過閾值的異常。

    Returns:
        boolean Series，True 表示該 bar 的 open 相對前一 close 跳幅過大
    """
    prev_close = df["close"].shift(1)
    jump = (df["open"] - prev_close).abs() / prev_close.replace(0, np.nan)
    return jump > threshold


# ── 便利函式 ──────────────────────────────────────────────────────────────────

def quality_report_from_store(
    symbol: str = "TX",
    start: str | None = None,
    end: str | None = None,
) -> DataQualityReport:
    """從 DuckDB DataStore 載入資料後執行驗證，直接印出品質報告。

    Args:
        symbol: 商品代碼
        start:  起始日期（"YYYY-MM-DD"）
        end:    結束日期（"YYYY-MM-DD"）
    """
    from src.core.data.store import DataStore

    with DataStore() as store:
        df = store.query(symbol, start=start, end=end)

    if df.empty:
        print(f"[WARN] DataStore 中找不到 {symbol} 的資料")
        return DataQualityReport()

    report = validate(df)
    print(report.summary())
    return report
