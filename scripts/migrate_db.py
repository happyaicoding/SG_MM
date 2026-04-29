"""scripts/migrate_db.py — 建立 SQLite + DuckDB Schema（冪等）。

Usage:
    python scripts/migrate_db.py
    python scripts/migrate_db.py --db-path ./data/sqlite/test.db --duckdb-path ./data/duckdb/test.duckdb

此腳本可重複執行（CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS）。
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import typer

logger = logging.getLogger(__name__)

# ── SQLite DDL（依外鍵依賴順序排列）─────────────────────────────────────────

SQLITE_DDL: list[str] = [
    # 資料層
    """CREATE TABLE IF NOT EXISTS minute_kbar (
        timestamp     TEXT NOT NULL,
        trading_day   TEXT NOT NULL,
        open          REAL NOT NULL,
        high          REAL NOT NULL,
        low           REAL NOT NULL,
        close         REAL NOT NULL,
        session_type  TEXT,
        PRIMARY KEY (timestamp)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_kbar_trading_day ON minute_kbar (trading_day)",

    """CREATE TABLE IF NOT EXISTS data_meta (
        key        TEXT PRIMARY KEY,
        value      TEXT,
        updated_at TEXT DEFAULT (datetime('now'))
    )""",

    # 策略層
    """CREATE TABLE IF NOT EXISTS strategies (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        name            TEXT UNIQUE NOT NULL,
        trading_session TEXT NOT NULL,
        logic_type      TEXT NOT NULL,
        timeframe       TEXT NOT NULL,
        direction       TEXT,
        status          TEXT NOT NULL DEFAULT 'pending',
        created_at      TEXT DEFAULT (datetime('now')),
        updated_at      TEXT DEFAULT (datetime('now'))
    )""",

    """CREATE TABLE IF NOT EXISTS strategy_yaml (
        strategy_id  INTEGER PRIMARY KEY,
        yaml_content TEXT NOT NULL,
        version      INTEGER DEFAULT 1,
        created_at   TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (strategy_id) REFERENCES strategies(id)
    )""",

    """CREATE TABLE IF NOT EXISTS strategy_el_code (
        strategy_id  INTEGER PRIMARY KEY,
        el_code      TEXT NOT NULL,
        pla_path     TEXT,
        created_at   TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (strategy_id) REFERENCES strategies(id)
    )""",

    # 回測層
    """CREATE TABLE IF NOT EXISTS backtest_results (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        strategy_id    INTEGER NOT NULL,
        sharpe         REAL,
        max_drawdown   REAL,
        profit_factor  REAL,
        win_rate       REAL,
        total_trades   INTEGER,
        net_profit     REAL,
        created_at     TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (strategy_id) REFERENCES strategies(id)
    )""",

    """CREATE TABLE IF NOT EXISTS wfa_windows (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        strategy_id  INTEGER NOT NULL,
        window_idx   INTEGER NOT NULL,
        is_start     TEXT,
        is_end       TEXT,
        oos_start    TEXT,
        oos_end      TEXT,
        oos_sharpe   REAL,
        created_at   TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (strategy_id) REFERENCES strategies(id)
    )""",

    """CREATE TABLE IF NOT EXISTS wfa_summary (
        strategy_id       INTEGER PRIMARY KEY,
        avg_oos_sharpe    REAL,
        overfitting_flag  INTEGER DEFAULT 0,
        windows_passed    INTEGER,
        created_at        TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (strategy_id) REFERENCES strategies(id)
    )""",

    # LLM / EL 層（quality_safeguards.md §10 完整欄位）
    """CREATE TABLE IF NOT EXISTS llm_calls (
        id                         INTEGER PRIMARY KEY AUTOINCREMENT,
        strategy_id                INTEGER,
        provider                   TEXT NOT NULL,
        model                      TEXT NOT NULL,
        prompt_step                TEXT NOT NULL,
        tokens_in                  INTEGER,
        tokens_out                 INTEGER,
        cost_usd                   REAL,
        latency_ms                 INTEGER,
        success                    INTEGER,
        error_message              TEXT,
        downstream_strategy_passed INTEGER,
        created_at                 TEXT DEFAULT (datetime('now'))
    )""",

    """CREATE TABLE IF NOT EXISTS el_validation_log (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        strategy_id  INTEGER NOT NULL,
        layer        INTEGER NOT NULL,
        passed       INTEGER NOT NULL,
        errors       TEXT,
        created_at   TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (strategy_id) REFERENCES strategies(id)
    )""",

    # 預算 / 追蹤層
    """CREATE TABLE IF NOT EXISTS budget_daily (
        date           TEXT PRIMARY KEY,
        total_cost_usd REAL DEFAULT 0,
        mode           TEXT DEFAULT 'normal',
        created_at     TEXT DEFAULT (datetime('now'))
    )""",

    """CREATE TABLE IF NOT EXISTS failure_log (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        strategy_id  INTEGER,
        stage        TEXT NOT NULL,
        reason       TEXT NOT NULL,
        details_json TEXT,
        created_at   TEXT DEFAULT (datetime('now'))
    )""",

    """CREATE TABLE IF NOT EXISTS quality_metrics (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        metric_name  TEXT NOT NULL,
        metric_value REAL NOT NULL,
        phase        TEXT,
        created_at   TEXT DEFAULT (datetime('now'))
    )""",
]

# ── DuckDB DDL（4 個 Collection + 6 個 HNSW 索引）────────────────────────────

DUCKDB_DDL: list[str] = [
    # strategies_developed：三向量 schema（核心 Collection）
    """CREATE TABLE IF NOT EXISTS strategies_developed (
        id                VARCHAR PRIMARY KEY,
        trading_session   VARCHAR NOT NULL,
        logic_type        VARCHAR NOT NULL,
        timeframe         VARCHAR NOT NULL,
        direction         VARCHAR,
        metadata_vector   FLOAT[1024],
        semantic_vector   FLOAT[1024],
        code_vector       FLOAT[1024],
        summary           TEXT,
        description       TEXT,
        notes             TEXT,
        market_assumption TEXT,
        el_code           TEXT,
        yaml_content      TEXT,
        sharpe            REAL,
        max_drawdown      REAL,
        profit_factor     REAL,
        overfitting_flag  BOOLEAN DEFAULT FALSE,
        created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""",
    "CREATE INDEX IF NOT EXISTS idx_dev_metadata ON strategies_developed USING HNSW (metadata_vector) WITH (metric = 'cosine')",
    "CREATE INDEX IF NOT EXISTS idx_dev_semantic ON strategies_developed USING HNSW (semantic_vector) WITH (metric = 'cosine')",
    "CREATE INDEX IF NOT EXISTS idx_dev_code ON strategies_developed USING HNSW (code_vector) WITH (metric = 'cosine')",

    # strategies_ideas：使用者提交的策略想法
    """CREATE TABLE IF NOT EXISTS strategies_ideas (
        id              VARCHAR PRIMARY KEY,
        source          TEXT,
        content         TEXT,
        semantic_vector FLOAT[1024],
        created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""",

    # strategies_failed：失敗反例（避免 LLM 重蹈覆轍）
    """CREATE TABLE IF NOT EXISTS strategies_failed (
        id               VARCHAR PRIMARY KEY,
        what_was_tried   TEXT NOT NULL,
        why_failed       TEXT NOT NULL,
        failure_metrics  TEXT,
        semantic_vector  FLOAT[1024],
        created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""",
    "CREATE INDEX IF NOT EXISTS idx_failed_semantic ON strategies_failed USING HNSW (semantic_vector) WITH (metric = 'cosine')",

    # knowledge_web：PTT/Mobile01 爬蟲結果（Phase 5 使用）
    """CREATE TABLE IF NOT EXISTS knowledge_web (
        id              VARCHAR PRIMARY KEY,
        url             VARCHAR UNIQUE,
        title           TEXT,
        content         TEXT,
        semantic_vector FLOAT[1024],
        created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""",
    "CREATE INDEX IF NOT EXISTS idx_web_semantic ON knowledge_web USING HNSW (semantic_vector) WITH (metric = 'cosine')",
]


def run_sqlite_migration(db_path: Path | None = None) -> int:
    """執行 SQLite 所有 DDL，建立 13 個資料表（冪等）。

    Args:
        db_path: 覆寫資料庫路徑，None 時從 get_settings() 取得。

    Returns:
        int: 0 = 成功，1 = 失敗。
    """
    try:
        from src.core.db import sqlite_conn

        with sqlite_conn(db_path) as conn:
            for ddl in SQLITE_DDL:
                conn.execute(ddl)
        logger.info("[OK] SQLite: 建立 %d 個 DDL 語句完成", len(SQLITE_DDL))
        return 0
    except Exception as exc:
        logger.error("[ERR] SQLite migration 失敗: %s", exc)
        return 1


def run_duckdb_migration(db_path: Path | None = None) -> int:
    """執行 DuckDB 所有 DDL，建立 4 個 Collection + 6 個 HNSW 索引（冪等）。

    Args:
        db_path: 覆寫資料庫路徑，None 時從 get_settings() 取得。

    Returns:
        int: 0 = 成功，1 = 失敗。
    """
    try:
        from src.core.db import duckdb_conn

        with duckdb_conn(db_path) as conn:
            for ddl in DUCKDB_DDL:
                conn.execute(ddl)
        logger.info("[OK] DuckDB: 建立 %d 個 DDL 語句完成（4 Collections + 6 HNSW 索引）", len(DUCKDB_DDL))
        return 0
    except Exception as exc:
        logger.error("[ERR] DuckDB migration 失敗: %s", exc)
        return 1


def main(db_path: str | None = None, duckdb_path: str | None = None) -> int:
    """執行完整 migration（SQLite + DuckDB）。

    Args:
        db_path: 覆寫 SQLite 路徑（字串）。
        duckdb_path: 覆寫 DuckDB 路徑（字串）。

    Returns:
        int: 0 = 全部成功，1 = 任一失敗。
    """
    sqlite_p = Path(db_path) if db_path else None
    duck_p = Path(duckdb_path) if duckdb_path else None

    rc1 = run_sqlite_migration(sqlite_p)
    rc2 = run_duckdb_migration(duck_p)
    return max(rc1, rc2)


app = typer.Typer(add_completion=False)


@app.command()
def cli(
    db_path: str = typer.Option(None, "--db-path", help="覆寫 SQLite 路徑"),
    duckdb_path: str = typer.Option(None, "--duckdb-path", help="覆寫 DuckDB 路徑"),
) -> None:
    """建立 SQLite + DuckDB Schema（冪等，可重複執行）。"""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    rc = main(db_path=db_path, duckdb_path=duckdb_path)
    raise typer.Exit(rc)


if __name__ == "__main__":
    app()
