"""動態策略載入器 — 從 .py 檔載入 BaseStrategy 子類別並實例化。

主要用於：AI 生成的策略剛存檔到 src/strategies/generated/ 後，需要立刻
拿來跑 vectorbt 初篩。為避免污染全域 registry，採用一次性動態 import。

Usage:
    from src.core.ai_engine.strategy_loader import load_strategy_from_file

    strategy = load_strategy_from_file(Path("src/strategies/generated/foo.py"))
    runner.run(strategy, ...)
"""
from __future__ import annotations

import importlib.util
import inspect
import logging
import sys
from pathlib import Path
from uuid import uuid4

from src.core.backtest.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


def load_strategy_from_file(
    filepath: Path,
    params: dict | None = None,
) -> BaseStrategy:
    """從 .py 檔動態載入第一個 BaseStrategy 子類別並實例化。

    Args:
        filepath: 策略 .py 檔絕對路徑
        params:   覆寫預設 PARAMS（None 則用類別預設值）

    Returns:
        BaseStrategy 子類別的實例

    Raises:
        FileNotFoundError: 檔案不存在
        ImportError:       無法 import（語法錯誤等）
        ValueError:        檔案中找不到 BaseStrategy 子類別

    Note:
        每次呼叫使用唯一的 module name（避免 sys.modules cache 衝突），
        但載入後仍會留在 sys.modules 中；同 session 連續載入多支策略時
        模組物件不會互相影響。
    """
    filepath = Path(filepath).resolve()
    if not filepath.exists():
        raise FileNotFoundError(f"策略檔不存在：{filepath}")

    # 唯一 module name，避免同檔重複載入時 cache 衝突
    mod_name = f"_aismart_dyn_{uuid4().hex[:8]}_{filepath.stem}"

    spec = importlib.util.spec_from_file_location(mod_name, filepath)
    if spec is None or spec.loader is None:
        raise ImportError(f"無法建立 module spec：{filepath}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        # 失敗時清掉 sys.modules
        sys.modules.pop(mod_name, None)
        raise ImportError(f"載入 {filepath.name} 失敗：{exc}") from exc

    # 找出第一個繼承 BaseStrategy 且非抽象的類別
    candidates: list[type[BaseStrategy]] = []
    for _, obj in inspect.getmembers(module, inspect.isclass):
        if (
            issubclass(obj, BaseStrategy)
            and obj is not BaseStrategy
            and obj.__module__ == mod_name  # 只取此模組定義的（排除 import 進來的）
        ):
            candidates.append(obj)

    if not candidates:
        raise ValueError(
            f"在 {filepath.name} 中找不到 BaseStrategy 子類別。"
            f"請確認策略類別有正確繼承 BaseStrategy。"
        )

    if len(candidates) > 1:
        logger.warning(
            "%s 中找到 %d 個策略類別，使用第一個：%s",
            filepath.name, len(candidates), candidates[0].__name__,
        )

    cls = candidates[0]
    instance = cls(params=params)
    logger.info("動態載入策略：%s (NAME=%s)", filepath.name, instance.NAME)
    return instance
