"""策略 Registry — 依名稱取得策略類別。

新增策略時，在 _REGISTRY 中登記即可。

Usage:
    from src.strategies.registry import get_strategy, list_strategies

    cls = get_strategy("MA_Cross")
    strategy = cls(params={"fast_period": 10, "slow_period": 30})
"""
from __future__ import annotations

import logging

from src.core.backtest.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)
from src.strategies.ma_cross import MACrossStrategy
from src.strategies.rsi_reversal import RSIReversalStrategy

_REGISTRY: dict[str, type[BaseStrategy]] = {
    MACrossStrategy.NAME:    MACrossStrategy,
    RSIReversalStrategy.NAME: RSIReversalStrategy,
}


def get_strategy(name: str) -> type[BaseStrategy]:
    """依策略名稱取得策略類別。

    Args:
        name: 策略 NAME（如 "MA_Cross"）

    Raises:
        KeyError: 策略不存在
    """
    if name not in _REGISTRY:
        available = list(_REGISTRY.keys())
        raise KeyError(f"策略 {name!r} 不存在。可用策略：{available}")
    return _REGISTRY[name]


def list_strategies() -> list[dict]:
    """回傳所有已註冊策略的 metadata 清單。"""
    results = []
    for name, cls in _REGISTRY.items():
        try:
            meta = cls().metadata()
        except Exception as exc:
            logger.warning("無法載入策略 %s 的 metadata：%s", name, exc)
            meta = {"name": name, "category": "", "params": {}, "description": ""}
        results.append(meta)
    return results
