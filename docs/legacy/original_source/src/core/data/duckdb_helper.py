"""DuckDB CSV 直查工具 — 不需預先匯入，直接查詢原始 CSV 檔案。

DuckDB read_csv_auto 可直接讀取 CSV，效能高且不佔 DuckDB 資料庫空間。
適用於：
    - 回測引擎在不匯入 DuckDB 的情況下快速讀取大型 CSV
    - 跨年度多檔合併查詢
    - 資料品質檢查前的快速預覽

Usage:
    from src.core.data.duckdb_helper import query_csv, query_csv_dir

    df = query_csv("data/raw/FITX_2023.csv", start_date="2023-01-01")
    df = query_csv_dir("data/raw/", start_date="2022-01-01", end_date="2024-12-31")
"""
from __future__ import annotations

import logging
from pathlib import Path

import duckdb
import pandas as pd

logger = logging.getLogger(__name__)


def query_csv(
    csv_path: str | Path,
    start_date: str | None = None,
    end_date: str | None = None,
    columns: list[str] | None = None,
) -> pd.DataFrame:
    """用 DuckDB read_csv_auto 直接查詢單一 CSV，回傳 DataFrame。

    支援格式 A（YYYYMMDD/HHMM 無 header）與格式 B（Date/Time 有 header）。
    格式 A 的日期過濾依 `date` 欄（整數）；格式 B 依 `Date` 欄（字串）。

    Args:
        csv_path:   CSV 檔案路徑
        start_date: 起始日期（含），格式 "YYYY-MM-DD"，僅格式 B 有效
        end_date:   結束日期（含），格式 "YYYY-MM-DD"，僅格式 B 有效
        columns:    指定回傳的欄位名稱（None = 全部）

    Returns:
        原始欄位 DataFrame（未標準化），供呼叫端自行處理
    """
    csv_path = str(Path(csv_path))
    col_select = ", ".join(columns) if columns else "*"

    conditions: list[str] = []

    # 格式 B 有 Date 欄，可直接過濾日期
    if start_date:
        conditions.append(f"CAST(Date AS VARCHAR) >= '{start_date}'")
    if end_date:
        conditions.append(f"CAST(Date AS VARCHAR) <= '{end_date}'")

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    sql = (
        f"SELECT {col_select} "
        f"FROM read_csv_auto('{csv_path}', header=auto) "
        f"{where} "
        f"ORDER BY 1, 2"   # 依前兩欄排序（date / time）
    )

    logger.debug("DuckDB query: %s", sql)

    try:
        df = duckdb.execute(sql).df()
        logger.info("DuckDB 查詢完成：%s → %d 行", Path(csv_path).name, len(df))
        return df
    except Exception as exc:
        logger.error("DuckDB 查詢失敗：%s — %s", csv_path, exc)
        raise


def query_csv_dir(
    csv_dir: str | Path,
    start_date: str | None = None,
    end_date: str | None = None,
    pattern: str = "*.csv",
) -> pd.DataFrame:
    """查詢目錄內所有 CSV（glob 合併），回傳合併後 DataFrame。

    使用 DuckDB glob 語法，效能優於逐檔讀取後 concat。

    Args:
        csv_dir:    CSV 目錄路徑
        start_date: 起始日期（含），格式 "YYYY-MM-DD"
        end_date:   結束日期（含），格式 "YYYY-MM-DD"
        pattern:    檔名 glob 樣式（預設 "*.csv"，可改 "*.txt"）

    Returns:
        合併後 DataFrame
    """
    csv_dir = Path(csv_dir)
    glob_path = str(csv_dir / pattern).replace("\\", "/")

    conditions: list[str] = []
    if start_date:
        conditions.append(f"CAST(Date AS VARCHAR) >= '{start_date}'")
    if end_date:
        conditions.append(f"CAST(Date AS VARCHAR) <= '{end_date}'")

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    sql = (
        f"SELECT * "
        f"FROM read_csv_auto('{glob_path}', header=auto, union_by_name=true) "
        f"{where} "
        f"ORDER BY 1, 2"
    )

    logger.debug("DuckDB dir query: %s", sql)

    try:
        df = duckdb.execute(sql).df()
        logger.info(
            "DuckDB 目錄查詢完成：%s → %d 行", csv_dir.name, len(df)
        )
        return df
    except Exception as exc:
        logger.error("DuckDB 目錄查詢失敗：%s — %s", csv_dir, exc)
        raise


def get_date_range(csv_path: str | Path) -> tuple[str, str] | None:
    """快速取得 CSV 的起訖日期（格式 B 專用）。

    Args:
        csv_path: CSV 檔案路徑

    Returns:
        (min_date, max_date) 字串 tuple，或 None（查詢失敗時）
    """
    csv_path = str(Path(csv_path))
    sql = (
        f"SELECT MIN(CAST(Date AS VARCHAR)), MAX(CAST(Date AS VARCHAR)) "
        f"FROM read_csv_auto('{csv_path}', header=auto)"
    )
    try:
        row = duckdb.execute(sql).fetchone()
        if row and row[0]:
            return str(row[0])[:10], str(row[1])[:10]
        return None
    except Exception as exc:
        logger.error("get_date_range 失敗：%s — %s", csv_path, exc)
        return None
