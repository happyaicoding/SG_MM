"""src/core/db.py — SQLite + DuckDB 連線管理。

SQLite：threading.local() 快取，每個 thread 一個長期連線（WAL mode + FK enabled）。
DuckDB：每次呼叫建立新連線（HNSW 索引狀態不跨連線，故不快取）。
兩個資料庫的路徑均從 get_settings() 取得，並自動建立父目錄。
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

import duckdb

from src.core.config import get_settings

_sqlite_local = threading.local()


def _sqlite_path(db_path: Path | None) -> Path:
    path = db_path if db_path is not None else get_settings().sqlite_path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _duckdb_path(db_path: Path | None) -> Path:
    path = db_path if db_path is not None else get_settings().duckdb_path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def get_sqlite_conn(db_path: Path | None = None) -> sqlite3.Connection:
    """取得 thread-local SQLite 連線（WAL mode，外鍵強制啟用）。

    Args:
        db_path: 資料庫檔案路徑，None 時從 get_settings() 取得。

    Returns:
        sqlite3.Connection: 已設定 WAL + FK 的連線，每個 thread 共享同一個實例。
    """
    path = _sqlite_path(db_path)
    key = str(path)
    conn = getattr(_sqlite_local, key, None)
    if conn is None:
        conn = sqlite3.connect(str(path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.commit()
        setattr(_sqlite_local, key, conn)
    return conn


@contextmanager
def sqlite_conn(db_path: Path | None = None) -> Generator[sqlite3.Connection, None, None]:
    """SQLite 連線 context manager（自動 commit/rollback）。

    Args:
        db_path: 資料庫檔案路徑，None 時從 get_settings() 取得。

    Yields:
        sqlite3.Connection: WAL + FK 已啟用的連線。
    """
    conn = get_sqlite_conn(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def get_duckdb_conn(db_path: Path | None = None) -> duckdb.DuckDBPyConnection:
    """建立新的 DuckDB 連線（每次呼叫建立，不快取）。

    Args:
        db_path: 資料庫檔案路徑，None 時從 get_settings() 取得。

    Returns:
        duckdb.DuckDBPyConnection: 已安裝 vss extension 的連線。

    Note:
        呼叫端負責在使用完畢後呼叫 conn.close()。
        DuckDB 連線不可跨 thread 共用。
    """
    path = _duckdb_path(db_path)
    conn = duckdb.connect(str(path), config={"hnsw_enable_experimental_persistence": True})
    conn.execute("INSTALL vss")
    conn.execute("LOAD vss")
    return conn


@contextmanager
def duckdb_conn(db_path: Path | None = None) -> Generator[duckdb.DuckDBPyConnection, None, None]:
    """DuckDB 連線 context manager（自動關閉）。

    Args:
        db_path: 資料庫檔案路徑，None 時從 get_settings() 取得。

    Yields:
        duckdb.DuckDBPyConnection: 已載入 vss extension 的連線。
    """
    conn = get_duckdb_conn(db_path)
    try:
        yield conn
    finally:
        conn.close()
