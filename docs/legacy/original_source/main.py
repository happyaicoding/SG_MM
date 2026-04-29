"""AISMART — AI 自動台指期策略開發系統 CLI 入口。

Usage:
    python main.py db init
    python main.py data init --csv-dir data/raw/
    python main.py web --port 8080
    python main.py run --mode full --cycles 10
    python main.py backtest --strategy <id>
    python main.py report --strategy <id> --format html
"""
from __future__ import annotations

import argparse
import sys


def cmd_db(args: argparse.Namespace) -> None:
    if args.db_cmd == "init":
        import logging
        logging.basicConfig(level=logging.INFO, format="%(message)s")

        from src.db.init_db import init_db
        engine = init_db()
        print(f"[OK] 資料庫初始化完成：{engine.url}")
        print("     建立資料表：strategies / backtest_results / alerts")
    else:
        print(f"未知的 db 子命令：{args.db_cmd}")
        sys.exit(1)


def cmd_data(args: argparse.Namespace) -> None:
    if args.data_cmd == "init":
        import logging
        logging.basicConfig(level=logging.INFO, format="%(message)s")

        from src.core.data.loader import load_csv_dir, merge_dataframes
        from src.core.data.store import DataStore

        print(f"[>>] 掃描目錄：{args.csv_dir}")
        dfs = load_csv_dir(args.csv_dir)
        if not dfs:
            print("[ERR] 找不到任何 CSV/TXT 檔案")
            return

        print(f"[>>] 合併 {len(dfs)} 個檔案...")
        combined = merge_dataframes(dfs)

        with DataStore() as store:
            n = store.write(combined, args.symbol)
            dr = store.date_range(args.symbol)
            print(f"[OK] 寫入完成：{n:,} 根 bar（{dr[0]} ~ {dr[1]}）")
    else:
        print(f"未知的 data 子命令：{args.data_cmd}")
        sys.exit(1)


def cmd_web(args: argparse.Namespace) -> None:
    import uvicorn
    from src.api.app import app
    uvicorn.run(app, host="0.0.0.0", port=args.port, reload=args.debug)


def cmd_run(args: argparse.Namespace) -> None:
    # TODO: implement Agent Loop orchestrator
    print(f"▶ 啟動 Agent Loop — mode={args.mode}, cycles={args.cycles}")


def cmd_backtest(args: argparse.Namespace) -> None:
    """執行 Python 初篩回測。

    --strategy  : 策略名稱（Registry NAME，如 MA_Cross）或 DB UUID
    --symbol    : 商品代碼（預設 TX）
    --start     : 回測起始日（預設 config split.train.start）
    --end       : 回測結束日（預設 config split.train.end）
    --params    : JSON 格式參數覆寫（例：'{"fast_period": 10}'）
    """
    import json
    import logging
    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    from src.core.backtest.runner import BacktestRunner
    from src.strategies.registry import get_strategy, list_strategies

    # 列出所有可用策略
    if args.strategy == "list":
        print("\n可用策略：")
        for meta in list_strategies():
            print(f"  {meta['name']:20s}  [{meta['category']}]  {meta['description']}")
        return

    # 解析參數覆寫
    override_params = None
    if args.params:
        try:
            override_params = json.loads(args.params)
        except json.JSONDecodeError:
            print(f"[ERR] --params 必須為合法 JSON，例：--params '{{\"fast_period\": 10}}'")
            sys.exit(1)

    # 取得策略
    try:
        cls = get_strategy(args.strategy)
    except KeyError as e:
        print(f"[ERR] {e}")
        print("      使用 `python main.py backtest --strategy list` 查看可用策略")
        sys.exit(1)

    strategy = cls(params=override_params)
    print(f"\n[>>] 回測策略：{strategy}")
    print(f"     商品={args.symbol}  週期={args.timeframe}  {args.start or '(config)'} ~ {args.end or '(config)'}")

    runner = BacktestRunner()
    result = runner.run(
        strategy,
        symbol=args.symbol,
        start=args.start,
        end=args.end,
        timeframe=args.timeframe,
    )
    print(result.summary())


def cmd_report(args: argparse.Namespace) -> None:
    # TODO: implement report generator
    print(f"▶ 產生報表 id={args.strategy}, format={args.format}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="AISMART — AI 自動化台指期策略開發系統",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # db
    p_db = sub.add_parser("db", help="資料庫管理")
    p_db_sub = p_db.add_subparsers(dest="db_cmd", required=True)
    p_db_sub.add_parser("init", help="初始化 SQLite 資料庫（data/aismart.db）")

    # data
    p_data = sub.add_parser("data", help="市場資料管理")
    p_data_sub = p_data.add_subparsers(dest="data_cmd", required=True)
    p_init = p_data_sub.add_parser("init", help="載入 CSV 到 DuckDB")
    p_init.add_argument("--csv-dir", default="data/raw/", help="CSV/TXT 目錄路徑")
    p_init.add_argument("--symbol", default="TX", help="商品代碼（預設 TX）")

    # web
    p_web = sub.add_parser("web", help="啟動 FastAPI Web API")
    p_web.add_argument("--port", type=int, default=8080)
    p_web.add_argument("--debug", action="store_true")

    # run
    p_run = sub.add_parser("run", help="啟動 Agent Loop")
    p_run.add_argument("--mode", choices=["full", "generate", "optimize"], default="full")
    p_run.add_argument("--cycles", type=int, default=5)

    # backtest
    p_bt = sub.add_parser("backtest", help="執行 Python 初篩回測")
    p_bt.add_argument("--strategy", required=True,
                      help="策略名稱（如 MA_Cross）或 'list' 查看所有可用策略")
    p_bt.add_argument("--symbol", default="TX", help="商品代碼（預設 TX）")
    p_bt.add_argument("--start",  default=None,  help="起始日 YYYY-MM-DD")
    p_bt.add_argument("--end",    default=None,  help="結束日 YYYY-MM-DD")
    p_bt.add_argument("--timeframe", default="1min",
                      help="K棒週期（預設 1min；可用：5m/15m/30m/60m/1h/D）")
    p_bt.add_argument("--params", default=None,  help="JSON 參數覆寫")

    # report
    p_rep = sub.add_parser("report", help="產生績效報表")
    p_rep.add_argument("--strategy", required=True, help="策略 ID")
    p_rep.add_argument("--format", choices=["html", "pdf"], default="html")

    return parser


if __name__ == "__main__":
    parser = build_parser()
    args = parser.parse_args()

    dispatch = {
        "db":       cmd_db,
        "data":     cmd_data,
        "web":      cmd_web,
        "run":      cmd_run,
        "backtest": cmd_backtest,
        "report":   cmd_report,
    }
    dispatch[args.command](args)
