"""src/core/data/etl.py — CSV → SQLite ETL（含 trading_day 計算）。

依 business_rules.md §2.3 §2.4：
  - 支援格式 A（"Date","Time"）和格式 B（"timestamp"）
  - trading_day 由 trading_day.py 的邏輯決定
  - session_type 為 "day_session" / "night_session" / "non_trading"
  - 寫入 minute_kbar 表（SQLite）

Usage:
    from src.core.data.etl import load_csv_to_sqlite
    load_csv_to_sqlite("./data/csv/20140101_20251231.csv")
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

import pandas as pd
from tqdm import tqdm

from src.core.config import get_settings
from src.core.data.trading_day import assign_trading_day, classify_session_type

logger = logging.getLogger(__name__)

# ── CSV 格式偵測 ──────────────────────────────────────────────────────────────

EXPECTED_COLS_A = {"Date", "Time", "Open", "High", "Low", "Close"}
EXPECTED_COLS_B = {"timestamp", "open", "high", "low", "close"}


def _detect_format(path: Path) -> str:
    """偵測 CSV 格式：A（Date+Time）或 B（timestamp）。"""
    first_line = Path(path).read_text(encoding="utf-8").splitlines()[0].strip()
    cols = set(first_line.replace('"', "").split(","))
    if EXPECTED_COLS_A.issubset(cols):
        return "A"
    if EXPECTED_COLS_B.issubset(cols):
        return "B"
    raise ValueError(f"無法偵測 CSV 格式（第一行：{first_line}）")


def _read_csv(path: Path) -> pd.DataFrame:
    """讀取 CSV 並標準化為 DataFrame（含 timestamp 欄位）。"""
    fmt = _detect_format(path)
    if fmt == "A":
        df = pd.read_csv(path, encoding="utf-8")
        df["timestamp"] = pd.to_datetime(
            df["Date"] + " " + df["Time"], format="%Y/%m/%d %H:%M:%S"
        )
    else:
        df = pd.read_csv(path, encoding="utf-8")
        df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df[["timestamp", "Open", "High", "Low", "Close"]].rename(
        columns={"Open": "open", "High": "high", "Low": "low", "Close": "close"}
    )


# ── ETL 主函式 ────────────────────────────────────────────────────────────────

def load_csv_to_sqlite(csv_path: str | Path, batch_size: int = 50_000) -> int:
    """將 CSV 檔案載入 SQLite minute_kbar 表（批次寫入，含 trading_day）。

    Args:
        csv_path: CSV 檔案路徑
        batch_size: 每批寫入筆數（default: 50,000）

    Returns:
        int: 總寫入筆數
    """
    path = Path(csv_path)
    logger.info("開始 ETL: %s", path.name)

    df = _read_csv(path)
    logger.info("  CSV 讀取完成: %d 筆", len(df))

    ts_index = df["timestamp"]
    trading_days: list[str] = []
    session_types: list[str] = []

    start = time.time()
    for ts in tqdm(ts_index, desc="  計算 trading_day"):
        td = assign_trading_day(ts, ts_index)
        trading_days.append(str(td) if td is not None else "")
        session_types.append(classify_session_type(ts))
    elapsed = time.time() - start
    logger.info("  trading_day 計算完成 (%.1f 秒)", elapsed)

    df["trading_day"] = trading_days
    df["session_type"] = session_types

    # 寫入 SQLite
    from src.core.db import sqlite_conn

    with sqlite_conn() as conn:
        conn.execute("DELETE FROM minute_kbar")  # 重新載入先清空

    total = 0
    for start_idx in tqdm(range(0, len(df), batch_size), desc="  寫入 SQLite"):
        batch = df.iloc[start_idx : start_idx + batch_size]
        rows = [
            (
                row["timestamp"].strftime("%Y-%m-%d %H:%M:%S"),
                row["trading_day"],
                row["open"],
                row["high"],
                row["low"],
                row["close"],
                row["session_type"],
            )
            for row in batch.itertuples(index=False)
        ]
        with sqlite_conn() as conn:
            conn.executemany(
                "INSERT INTO minute_kbar "
                "(timestamp, trading_day, open, high, low, close, session_type) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        total += len(rows)

    logger.info("ETL 完成: 寫入 %d 筆", total)
    return total


def get_row_count() -> int:
    """回傳 minute_kbar 目前筆數。"""
    from src.core.db import sqlite_conn
    with sqlite_conn() as conn:
        row = conn.execute("SELECT COUNT(*) FROM minute_kbar").fetchone()
        return int(row[0]) if row else 0
