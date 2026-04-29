"""data_adapter.py — 橋接 AISMART DataStore 與 strategygen 子專案。

此模組是唯一允許存取主專案 src.core.data.* 的地方。
其餘子專案程式碼應透過這裡的公開函式取得資料，不可直接 import 主專案。

公開 API:
    get_ohlc(symbol, start, end)  -> pd.DataFrame
    resample_ohlc(df, timeframe)  -> pd.DataFrame
    get_aismart_root()            -> Path
    get_generated_dir()           -> Path
"""
from __future__ import annotations

import sys
import logging
from functools import lru_cache
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# src/strategygen/data_adapter.py
#   parents[0] = src/strategygen/
#   parents[1] = src/
#   parents[2] = subprojects/strategygen/  ← 子專案根目錄
_SUBPROJECT_ROOT = Path(__file__).resolve().parents[2]
_CONFIG_PATH = _SUBPROJECT_ROOT / "config.yaml"


@lru_cache(maxsize=1)
def _load_config() -> dict:
    with open(_CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@lru_cache(maxsize=1)
def get_aismart_root() -> Path:
    """解析並回傳 AISMART 主專案根目錄的絕對路徑。"""
    raw = _load_config().get("aismart_root", "../../")
    root = (_SUBPROJECT_ROOT / raw).resolve()
    if not root.exists():
        raise FileNotFoundError(
            f"AISMART root not found at {root}. "
            f"Check 'aismart_root' in {_CONFIG_PATH}"
        )
    return root


@lru_cache(maxsize=1)
def get_generated_dir() -> Path:
    """回傳主專案生成策略的目錄路徑（自動建立）。"""
    raw = _load_config().get("generated_strategies_dir", "../../src/strategies/generated")
    d = (_SUBPROJECT_ROOT / raw).resolve()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ensure_aismart_on_path() -> None:
    """將 AISMART 根目錄加入 sys.path（冪等操作）。"""
    root = str(get_aismart_root())
    if root not in sys.path:
        sys.path.insert(0, root)
        logger.debug("Added AISMART root to sys.path: %s", root)


def get_ohlc(symbol: str, start: str, end: str):
    """從 AISMART DataStore 查詢 OHLC bar 資料。

    注意：DataStore 不實作 context manager，使用 try/finally 確保連線關閉。

    Args:
        symbol: 商品代碼（如 "TX"）
        start:  起始日期 "YYYY-MM-DD"
        end:    結束日期 "YYYY-MM-DD"

    Returns:
        pd.DataFrame，DatetimeIndex，欄位 open/high/low/close
    """
    _ensure_aismart_on_path()
    from src.core.data.store import DataStore  # 主專案（動態 import 避免循環依賴）
    store = DataStore()
    try:
        return store.query(symbol, start=start, end=end)
    finally:
        store._con.close()


def resample_ohlc(df, timeframe: str):
    """將 OHLC DataFrame 重新採樣至目標週期。

    Args:
        df:        來自 get_ohlc() 的 DataFrame
        timeframe: 目標週期字串（如 "5m" / "15min" / "60m" / "D"）

    Returns:
        重採樣後的 pd.DataFrame
    """
    _ensure_aismart_on_path()
    from src.core.data.resample import resample_ohlc as _resample  # 主專案
    return _resample(df, timeframe)
