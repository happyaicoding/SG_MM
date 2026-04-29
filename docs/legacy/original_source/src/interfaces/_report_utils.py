"""報表共用工具函式（html_report / pdf_report 內部使用）。"""
from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)


def sanitize_filename(value: str) -> str:
    """將任意字串轉為安全的檔名（僅保留英數字、連字號、底線）。"""
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in value)


def calc_monthly_pnl(equity: pd.Series) -> pd.Series | None:
    """計算月度損益：當月最後一根 equity - 上月最後一根 equity。

    Returns:
        月度損益 Series（index=月末日期）；計算失敗時回傳 None
    """
    try:
        pnl = equity.resample("ME").last().diff().dropna()
        return pnl if len(pnl) else None
    except Exception as exc:
        logger.warning("月度損益計算失敗：%s", exc)
        return None
