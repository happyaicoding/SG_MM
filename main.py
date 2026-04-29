"""main.py — AISMART CLI 入口（Typer）。

Usage:
    python main.py db init
    python main.py data init --csv-dir data/csv/
    python main.py data count
    python main.py web --port 8000
    python main.py --help
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import typer

from src.core.config import get_settings
from src.core.data.etl import get_row_count, load_csv_to_sqlite

app = typer.Typer(add_completion=False)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)


def _ensure_data_dirs() -> None:
    """確保所有資料目錄存在。"""
    settings = get_settings()
    settings.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    settings.duckdb_path.parent.mkdir(parents=True, exist_ok=True)


@app.command()
def db_init() -> None:
    """初始化 SQLite + DuckDB Schema（執行 migration）。"""
    _ensure_data_dirs()
    from scripts.migrate_db import main as migrate_main

    logging.info("=== SQLite Schema 建立 ===")
    rc1 = _run_sqlite_migration()
    logging.info("=== DuckDB Schema 建立 ===")
    rc2 = _run_duckdb_migration()

    if rc1 == 0 and rc2 == 0:
        logging.info("DB init 完成")
    else:
        logging.error("DB init 有錯誤，請檢查上方訊息")
        raise typer.Exit(1)


def _run_sqlite_migration() -> int:
    from scripts.migrate_db import run_sqlite_migration
    return run_sqlite_migration()


def _run_duckdb_migration() -> int:
    from scripts.migrate_db import run_duckdb_migration
    return run_duckdb_migration()


@app.command()
def data_init(csv_dir: str = typer.Option("data/csv", "--csv-dir", help="CSV 目錄")) -> None:
    """執行 CSV ETL，載入 SQLite minute_kbar 表。"""
    _ensure_data_dirs()
    # 先確保 schema 已建立
    rc1 = _run_sqlite_migration()
    if rc1 != 0:
        logging.error("SQLite migration 失敗，請先 python main.py db init")
        raise typer.Exit(1)

    csv_path = Path(csv_dir)
    if not csv_path.is_dir():
        logging.error("CSV 目錄不存在: %s", csv_dir)
        raise typer.Exit(1)

    csv_files = list(csv_path.glob("*.csv"))
    if not csv_files:
        logging.error("CSV 目錄中找不到任何 .csv 檔案")
        raise typer.Exit(1)

    total_all = 0
    for f in csv_files:
        logging.info("處理檔案: %s", f.name)
        n = load_csv_to_sqlite(f)
        total_all += n

    logging.info("ETL 完成，總寫入 %d 筆", total_all)


@app.command()
def data_count() -> None:
    """顯示 minute_kbar 目前筆數。"""
    n = get_row_count()
    logging.info("minute_kbar 目前筆數: %d", n)


@app.command()
def web(port: int = typer.Option(8000, "--port", help="API Port")) -> None:
    """啟動 FastAPI Web 服務（占位，Phase 4 實作）。"""
    logging.info("Web 服務將在 Phase 4 實作，目前可用 port: %d", port)
    # Phase 4 替換為：
    # import uvicorn
    # from src.web.main import app
    # uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    app()
