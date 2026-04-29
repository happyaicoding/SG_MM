"""Python 初篩回測 Runner — 策略 → 訊號 → 回測 → 門檻篩選 → SQLite。

完整流程：
    1. 從 DataStore 載入指定區間的 OHLC 資料
    2. 呼叫策略的 generate_signals()
    3. 執行 python_bt.run_backtest()（vectorbt / 簡易備援）
    4. 比對 config.yaml python_filter 門檻
    5. 結果寫入 SQLite backtest_results 表
    6. 回傳 BacktestRunResult

Usage:
    from src.core.backtest.runner import BacktestRunner

    runner = BacktestRunner()
    result = runner.run(strategy, symbol="TX", start="2015-01-01", end="2021-12-31")
    print(result.summary())
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

import yaml
from sqlalchemy.orm import Session

from src.core.backtest.base_strategy import BaseStrategy
from src.core.backtest.python_bt import run_backtest
from src.core.data.store import DataStore

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config.yaml"

if not _CONFIG_PATH.exists():
    raise FileNotFoundError(
        f"找不到設定檔：{_CONFIG_PATH}。"
        "請確認 config.yaml 位於專案根目錄。"
    )


def _load_config() -> dict:
    with open(_CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


@dataclass
class BacktestRunResult:
    """回測執行結果。"""
    strategy_name: str
    strategy_id:   str | None    # DB 中的策略 UUID（若有入庫）
    result_id:     str           # 回測結果 UUID
    symbol:        str
    start:         str
    end:           str

    # 績效指標
    sharpe_ratio:   float
    max_drawdown:   float
    profit_factor:  float
    total_trades:   int
    win_rate:       float
    annual_return:  float
    final_equity:   float

    # 門檻判斷
    passed_filter:  bool
    filter_reasons: list[str] = field(default_factory=list)  # 未通過的原因

    def summary(self) -> str:
        status = "[PASS]" if self.passed_filter else "[FAIL]"
        lines = [
            "=" * 55,
            f"  {status} {self.strategy_name}  ({self.start} ~ {self.end})",
            "=" * 55,
            f"  Sharpe Ratio  : {self.sharpe_ratio:>8.3f}",
            f"  Max Drawdown  : {self.max_drawdown:>8.1%}",
            f"  Profit Factor : {self.profit_factor:>8.3f}",
            f"  Total Trades  : {self.total_trades:>8,}",
            f"  Win Rate      : {self.win_rate:>8.1%}",
            f"  Annual Return : {self.annual_return:>8.1%}",
            f"  Final Equity  : NT${self.final_equity:>12,.0f}",
        ]
        if not self.passed_filter:
            lines.append(f"  未通過原因: {', '.join(self.filter_reasons)}")
        lines.append("=" * 55)
        return "\n".join(lines)


class BacktestRunner:
    """Python 初篩回測執行器。

    Args:
        db_session: SQLAlchemy Session（若提供則將結果寫入 DB，否則僅回傳）
    """

    def __init__(self, db_session: Session | None = None) -> None:
        self._cfg = _load_config()
        self._db = db_session

    # ── 主執行方法 ────────────────────────────────────────────────

    def run(
        self,
        strategy: BaseStrategy,
        symbol: str = "TX",
        start: str | None = None,
        end: str | None = None,
        timeframe: str = "1min",
        strategy_db_id: str | None = None,
    ) -> BacktestRunResult:
        """執行完整初篩流程。

        Args:
            strategy:       BaseStrategy 實例
            symbol:         商品代碼
            start:          回測起始日（預設從 config split.train.start）
            end:            回測結束日（預設從 config split.train.end）
            timeframe:      K 棒週期（預設 1min；支援 5m/15m/30m/60m/1h/D 等）
            strategy_db_id: 策略在 SQLite 的 UUID（供寫入回測結果關聯用）

        Returns:
            BacktestRunResult
        """
        cfg_split = self._cfg.get("split", {})
        start = start or cfg_split.get("train", {}).get("start", "2015-01-01")
        end   = end   or cfg_split.get("train", {}).get("end",   "2021-12-31")

        logger.info(
            "開始回測：%s  %s ~ %s  [%s]",
            strategy.NAME, start, end, timeframe,
        )

        # Step 1：載入資料
        df = self._load_data(symbol, start, end)
        if df.empty:
            raise ValueError(f"DataStore 中找不到 {symbol} {start}~{end} 的資料")

        # Step 1b：重新採樣（1min → 目標週期）
        if timeframe not in ("1min", "1m", "1"):
            from src.core.data.resample import resample_ohlc
            try:
                df = resample_ohlc(df, timeframe)
            except Exception as exc:
                raise ValueError(
                    f"無法將資料重新採樣為週期 {timeframe!r}：{exc}。"
                    "支援格式範例：5m / 15m / 30m / 60m / 1h / 2h / D"
                ) from exc

        # Step 2：產生訊號（已含 shift(1)）
        signals = strategy.generate_signals(df)

        # Step 3：回測
        contract = self._cfg.get("contract", {})
        params = {
            "point_value": contract.get("point_value", 200),
            "commission":  contract.get("commission", 100),
            "slippage":    contract.get("slippage", 1),
            "init_cash":   contract.get("initial_capital", 500_000),
        }
        bt_result = run_backtest(signals, df, params)

        # Step 4：門檻篩選
        passed, reasons = self._check_filter(bt_result)

        result = BacktestRunResult(
            strategy_name  = strategy.NAME,
            strategy_id    = strategy_db_id,
            result_id      = str(uuid4()),
            symbol         = symbol,
            start          = start,
            end            = end,
            sharpe_ratio   = bt_result["sharpe"],
            max_drawdown   = bt_result["max_drawdown"],
            profit_factor  = bt_result["profit_factor"],
            total_trades   = bt_result["total_trades"],
            win_rate       = bt_result["win_rate"],
            annual_return  = bt_result["annual_return"],
            final_equity   = bt_result["final_equity"],
            passed_filter  = passed,
            filter_reasons = reasons,
        )

        # Step 5：寫入 DB（若有 session）
        if self._db and strategy_db_id:
            self._write_to_db(result, bt_result)

        logger.info(
            "回測完成：%s | Sharpe=%.2f MaxDD=%.1f%% PF=%.2f Trades=%d | %s",
            strategy.NAME,
            result.sharpe_ratio,
            result.max_drawdown * 100,
            result.profit_factor,
            result.total_trades,
            "PASS" if passed else f"FAIL({', '.join(reasons)})",
        )
        return result

    # ── 門檻篩選 ──────────────────────────────────────────────────

    def _check_filter(self, bt_result: dict) -> tuple[bool, list[str]]:
        """比對 python_filter 門檻，回傳 (passed, [fail_reasons])。"""
        f = self._cfg.get("python_filter", {})
        reasons: list[str] = []

        if bt_result["sharpe"] < f.get("sharpe_min", 1.2):
            reasons.append(
                f"Sharpe {bt_result['sharpe']:.2f} < {f.get('sharpe_min', 1.2)}"
            )
        if bt_result["max_drawdown"] > f.get("max_drawdown_max", 0.35):
            reasons.append(
                f"MaxDD {bt_result['max_drawdown']:.1%} > {f.get('max_drawdown_max', 0.35):.0%}"
            )
        if bt_result["profit_factor"] < f.get("profit_factor_min", 1.0):
            reasons.append(
                f"PF {bt_result['profit_factor']:.2f} < {f.get('profit_factor_min', 1.0)}"
            )
        if bt_result["total_trades"] < f.get("min_trades", 80):
            reasons.append(
                f"Trades {bt_result['total_trades']} < {f.get('min_trades', 80)}"
            )

        return len(reasons) == 0, reasons

    # ── 資料載入 ──────────────────────────────────────────────────

    def _load_data(self, symbol: str, start: str, end: str):
        with DataStore() as store:
            return store.query(symbol, start=start, end=end)

    # ── 寫入 DB ───────────────────────────────────────────────────

    def _write_to_db(self, result: BacktestRunResult, raw: dict) -> None:
        """將回測結果寫入 SQLite backtest_results 表。"""
        try:
            from src.db.models import BacktestResult, Strategy
            bt = BacktestResult(
                id             = result.result_id,
                strategy_id    = result.strategy_id,
                engine         = "python",
                sharpe_ratio   = result.sharpe_ratio,
                max_drawdown   = result.max_drawdown,
                profit_factor  = result.profit_factor,
                total_trades   = result.total_trades,
                win_rate       = result.win_rate,
                overfitting_flag = False,
            )
            self._db.add(bt)

            # 更新策略狀態
            strategy_row = self._db.get(Strategy, result.strategy_id)
            if strategy_row:
                strategy_row.status = "reviewing" if result.passed_filter else "draft"

            self._db.commit()
            logger.info("回測結果已寫入 DB：result_id=%s", result.result_id)
        except Exception as exc:
            logger.error("寫入 DB 失敗：%s", exc)
            self._db.rollback()
            raise RuntimeError(f"無法持久化回測結果：{exc}") from exc
