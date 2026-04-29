"""DuckDB 封裝 — 分K資料寫入、區間查詢、快取管理。

資料表結構：
    bars(symbol TEXT, datetime TIMESTAMP, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE)
    PK: (symbol, datetime)
"""
from __future__ import annotations

import logging
from pathlib import Path

import duckdb
import pandas as pd

logger = logging.getLogger(__name__)

# src/core/data/ → parents[3] = 專案根目錄
_DEFAULT_DB = Path(__file__).resolve().parents[3] / "db" / "market.duckdb"

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS bars (
    symbol   TEXT      NOT NULL,
    datetime TIMESTAMP NOT NULL,
    open     DOUBLE    NOT NULL,
    high     DOUBLE    NOT NULL,
    low      DOUBLE    NOT NULL,
    close    DOUBLE    NOT NULL,
    PRIMARY KEY (symbol, datetime)
)
"""


class DataStore:
    def __init__(self, db_path: str | Path = _DEFAULT_DB) -> None:
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(db_path))
        self._con.execute(_CREATE_TABLE)
        logger.info("DataStore 初始化：%s", db_path)

    # ---------------------------------------------------------------- write
    def write(self, df: pd.DataFrame, symbol: str) -> int:
        """寫入 bar 資料（upsert：已存在則覆蓋）。回傳寫入筆數。

        Args:
            df: DatetimeIndex，欄位 open/high/low/close
            symbol: 商品代碼（如 "TX"）
        """
        if df.empty:
            return 0

        tmp = df[["open", "high", "low", "close"]].copy().reset_index()
        tmp.columns = ["datetime", "open", "high", "low", "close"]
        tmp.insert(0, "symbol", symbol)

        # DuckDB upsert via INSERT OR REPLACE
        self._con.execute("""
            INSERT OR REPLACE INTO bars
            SELECT symbol, datetime, open, high, low, close FROM tmp
        """)
        count = len(tmp)
        logger.info("寫入 %d 根 bar（%s）", count, symbol)
        return count

    # ---------------------------------------------------------------- query
    def query(
        self,
        symbol: str,
        start: str | None = None,
        end: str | None = None,
        session: str = "all",
    ) -> pd.DataFrame:
        """查詢指定範圍 bar 資料。

        Args:
            symbol: 商品代碼
            start:  起始時間（含），如 "2022-01-01" 或 "2022-01-01 08:45"
            end:    結束時間（含），如 "2024-12-31"
            session: "all" / "day"（08:45-13:44）/ "night"（15:00-04:59）

        Returns:
            DatetimeIndex DataFrame，欄位 open/high/low/close
        """
        conditions = ["symbol = ?"]
        params: list = [symbol]

        if start:
            conditions.append("datetime >= ?")
            params.append(pd.Timestamp(start))
        if end:
            conditions.append("datetime <= ?")
            params.append(pd.Timestamp(end).replace(hour=23, minute=59, second=59))

        where = " AND ".join(conditions)
        sql = f"SELECT datetime, open, high, low, close FROM bars WHERE {where} ORDER BY datetime"

        df = self._con.execute(sql, params).df()
        if df.empty:
            return pd.DataFrame(columns=["open", "high", "low", "close"])

        df["datetime"] = pd.to_datetime(df["datetime"])
        df = df.set_index("datetime")

        # 時段過濾
        if session != "all":
            from data.loader import filter_trading_sessions
            if session == "day":
                t = df.index.time
                df = df[(t >= pd.Timestamp("08:45").time()) & (t < pd.Timestamp("13:45").time())]
            elif session == "night":
                t = df.index.time
                df = df[(t >= pd.Timestamp("15:00").time()) | (t < pd.Timestamp("05:00").time())]

        logger.debug("查詢 %s [%s ~ %s] session=%s → %d 根", symbol, start, end, session, len(df))
        return df

    # ---------------------------------------------------------------- utils
    def symbols(self) -> list[str]:
        """回傳資料庫中所有商品代碼。"""
        rows = self._con.execute("SELECT DISTINCT symbol FROM bars ORDER BY symbol").fetchall()
        return [r[0] for r in rows]

    def date_range(self, symbol: str) -> tuple[pd.Timestamp, pd.Timestamp] | None:
        """回傳指定商品的資料起訖時間。"""
        row = self._con.execute(
            "SELECT MIN(datetime), MAX(datetime) FROM bars WHERE symbol = ?", [symbol]
        ).fetchone()
        if row and row[0]:
            return pd.Timestamp(row[0]), pd.Timestamp(row[1])
        return None

    def count(self, symbol: str) -> int:
        """回傳指定商品的總 bar 數。"""
        return self._con.execute(
            "SELECT COUNT(*) FROM bars WHERE symbol = ?", [symbol]
        ).fetchone()[0]

    def close(self) -> None:
        self._con.close()

    def __enter__(self) -> "DataStore":
        return self

    def __exit__(self, *_) -> None:
        self.close()
