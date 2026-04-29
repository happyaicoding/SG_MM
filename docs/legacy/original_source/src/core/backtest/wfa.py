"""Walk-Forward 分析（WFA） — 滾動視窗 IS/OOS 驗證，偵測過擬合。

流程：
    1. 將資料切成多個「訓練（IS）+ 測試（OOS）」滾動視窗
    2. 在每個 IS 視窗枚舉 param_grid，選出最佳參數
    3. 以最佳參數在對應 OOS 視窗回測
    4. 計算每視窗的 IS Sharpe / OOS Sharpe / 降解比
    5. 最終判斷：OOS/IS 平均比 < oos_is_ratio_min → 標記 overfitting

Usage:
    from src.core.backtest.wfa import walk_forward, WFAResult

    result = walk_forward(
        strategy_cls = MACrossStrategy,
        df           = df_60m,
        param_grid   = {"fast_period": [10, 20, 30], "slow_period": [40, 60, 80]},
        train_months = 24,
        test_months  = 6,
    )
    print(result.summary())
    print("過擬合：", result.overfitting)
"""
from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING

import pandas as pd

from src.core.backtest.python_bt import run_backtest

if TYPE_CHECKING:
    from src.core.backtest.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)

_OOS_IS_RATIO_MIN = 0.6   # 低於此值視為過擬合


@dataclass
class WFAWindow:
    """單一 WFA 視窗結果。"""
    window_idx:  int
    is_start:    str
    is_end:      str
    oos_start:   str
    oos_end:     str
    best_params: dict
    is_sharpe:   float
    oos_sharpe:  float
    ratio:       float    # oos_sharpe / is_sharpe（越接近 1.0 越好）


@dataclass
class WFAResult:
    """Walk-Forward 分析整體結果。"""
    strategy_name:    str
    windows:          list[WFAWindow] = field(default_factory=list)
    avg_is_sharpe:    float = 0.0
    avg_oos_sharpe:   float = 0.0
    avg_ratio:        float = 0.0
    overfitting:      bool  = False
    oos_is_ratio_min: float = _OOS_IS_RATIO_MIN

    def summary(self) -> str:
        lines = [
            "=" * 55,
            f"  Walk-Forward Analysis — {self.strategy_name}",
            f"  視窗數：{len(self.windows)}",
            "=" * 55,
            f"  平均 IS Sharpe  : {self.avg_is_sharpe:>8.3f}",
            f"  平均 OOS Sharpe : {self.avg_oos_sharpe:>8.3f}",
            f"  OOS/IS 比       : {self.avg_ratio:>8.3f}  "
            f"（門檻 >= {self.oos_is_ratio_min}）",
            f"  過擬合判定      : {'[WARNING] 疑似過擬合' if self.overfitting else '[OK] 通過'}",
            "-" * 55,
        ]
        for w in self.windows:
            lines.append(
                f"  [{w.window_idx:02d}] IS={w.is_start[:7]}~{w.is_end[:7]}"
                f"  OOS={w.oos_start[:7]}~{w.oos_end[:7]}"
                f"  IS={w.is_sharpe:.2f}  OOS={w.oos_sharpe:.2f}"
                f"  ratio={w.ratio:.2f}"
                f"  params={w.best_params}"
            )
        lines.append("=" * 55)
        return "\n".join(lines)


def walk_forward(
    strategy_cls,
    df: pd.DataFrame,
    param_grid: dict,
    train_months: int = 24,
    test_months: int = 6,
    bt_params: dict | None = None,
    oos_is_ratio_min: float = _OOS_IS_RATIO_MIN,
) -> WFAResult:
    """執行滾動 Walk-Forward 最佳化。

    Args:
        strategy_cls:     BaseStrategy 子類別（未實例化）
        df:               DatetimeIndex OHLC DataFrame（1分K 或已重採樣）
        param_grid:       參數格子，格式 {"param": [v1, v2, ...]}
                          例：{"fast_period": [10, 20], "slow_period": [40, 60]}
        train_months:     IS 訓練視窗長度（月，預設 24）
        test_months:      OOS 測試視窗長度（月，預設 6）
        bt_params:        run_backtest 合約參數覆寫
        oos_is_ratio_min: OOS/IS Sharpe 比的最低門檻（低於此 → overfitting）

    Returns:
        WFAResult
    """
    if df.empty:
        raise ValueError("df 不能為空")

    combos = _expand_grid(param_grid)
    if not combos:
        raise ValueError("param_grid 不能為空")

    _bt_params = bt_params or {
        "point_value": 200,
        "commission":  100,
        "slippage":    1,
        "init_cash":   500_000,
    }

    windows_data = _build_windows(df, train_months, test_months)
    if not windows_data:
        raise ValueError(
            f"資料不足以切出任何 WFA 視窗（需要至少 {train_months + test_months} 個月）"
        )

    result = WFAResult(strategy_name=strategy_cls.NAME)
    result.oos_is_ratio_min = oos_is_ratio_min

    for idx, (is_df, oos_df) in enumerate(windows_data, start=1):
        is_start  = str(is_df.index[0].date())
        is_end    = str(is_df.index[-1].date())
        oos_start = str(oos_df.index[0].date())
        oos_end   = str(oos_df.index[-1].date())

        logger.info(
            "WFA 視窗 %02d：IS=%s~%s  OOS=%s~%s  組合數=%d",
            idx, is_start, is_end, oos_start, oos_end, len(combos),
        )

        # IS：枚舉所有參數組合，選最佳 Sharpe
        best_params, best_is_sharpe = _optimize_on_is(
            strategy_cls, is_df, combos, _bt_params
        )

        # OOS：以最佳參數在測試集評估
        oos_sharpe = _eval_oos(strategy_cls, oos_df, best_params, _bt_params)

        ratio = (oos_sharpe / best_is_sharpe) if best_is_sharpe > 0 else 0.0

        result.windows.append(WFAWindow(
            window_idx  = idx,
            is_start    = is_start,
            is_end      = is_end,
            oos_start   = oos_start,
            oos_end     = oos_end,
            best_params = best_params,
            is_sharpe   = best_is_sharpe,
            oos_sharpe  = oos_sharpe,
            ratio       = ratio,
        ))

        logger.info(
            "  最佳參數=%s  IS=%.2f  OOS=%.2f  ratio=%.2f",
            best_params, best_is_sharpe, oos_sharpe, ratio,
        )

    # 彙總統計
    if result.windows:
        result.avg_is_sharpe  = sum(w.is_sharpe  for w in result.windows) / len(result.windows)
        result.avg_oos_sharpe = sum(w.oos_sharpe for w in result.windows) / len(result.windows)
        result.avg_ratio      = sum(w.ratio      for w in result.windows) / len(result.windows)
        result.overfitting    = result.avg_ratio < oos_is_ratio_min

    return result


# ── 內部工具函式 ───────────────────────────────────────────────────

def _expand_grid(param_grid: dict) -> list[dict]:
    """將 param_grid 展開為所有參數組合列表。"""
    keys = list(param_grid.keys())
    values = list(param_grid.values())
    return [dict(zip(keys, combo)) for combo in itertools.product(*values)]


def _build_windows(
    df: pd.DataFrame,
    train_months: int,
    test_months: int,
) -> list[tuple[pd.DataFrame, pd.DataFrame]]:
    """按月份切割滾動視窗，回傳 (is_df, oos_df) 列表。"""
    start = df.index[0]
    end   = df.index[-1]

    windows = []
    cursor = start

    while True:
        is_end   = cursor + pd.DateOffset(months=train_months)
        oos_end  = is_end + pd.DateOffset(months=test_months)

        if oos_end > end + timedelta(days=1):
            break

        is_df  = df[(df.index >= cursor) & (df.index < is_end)]
        oos_df = df[(df.index >= is_end) & (df.index < oos_end)]

        if len(is_df) > 100 and len(oos_df) > 10:
            windows.append((is_df, oos_df))

        cursor += pd.DateOffset(months=test_months)  # 向前滾動一個 OOS 長度

    return windows


def _optimize_on_is(
    strategy_cls,
    is_df: pd.DataFrame,
    combos: list[dict],
    bt_params: dict,
) -> tuple[dict, float]:
    """在 IS 資料上枚舉參數組合，回傳（最佳參數, 最佳 Sharpe）。"""
    best_params = combos[0]
    best_sharpe = float("-inf")

    for params in combos:
        strategy = strategy_cls(params=params)
        if not strategy.validate_params(params):
            continue
        try:
            signals = strategy.generate_signals(is_df)
            r = run_backtest(signals, is_df, bt_params)
            sharpe = r["sharpe"]
        except Exception as exc:
            logger.debug("IS 回測失敗（params=%s）：%s", params, exc)
            continue

        if sharpe > best_sharpe:
            best_sharpe = sharpe
            best_params = params

    return best_params, max(best_sharpe, 0.0)


def _eval_oos(
    strategy_cls,
    oos_df: pd.DataFrame,
    params: dict,
    bt_params: dict,
) -> float:
    """在 OOS 資料上以指定參數回測，回傳 Sharpe（失敗時回傳 0.0）。"""
    try:
        strategy = strategy_cls(params=params)
        signals = strategy.generate_signals(oos_df)
        r = run_backtest(signals, oos_df, bt_params)
        return r["sharpe"]
    except Exception as exc:
        logger.debug("OOS 回測失敗（params=%s）：%s", params, exc)
        return 0.0
