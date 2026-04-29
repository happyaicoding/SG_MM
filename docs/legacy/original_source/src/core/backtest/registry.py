"""策略註冊表 — 管理所有策略的索引與狀態。"""
from __future__ import annotations

from typing import Type

from strategies.base import BaseStrategy

_registry: dict[str, Type[BaseStrategy]] = {}


def register(cls: Type[BaseStrategy]) -> Type[BaseStrategy]:
    """裝飾器：將策略類別註冊到全域 registry。"""
    _registry[cls.NAME] = cls
    return cls


def get(name: str) -> Type[BaseStrategy]:
    if name not in _registry:
        raise KeyError(f"策略未找到：{name!r}，可用：{list(_registry)}")
    return _registry[name]


def list_all() -> list[str]:
    return sorted(_registry.keys())
