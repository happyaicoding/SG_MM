"""CSV / TXT 分K資料載入與標準化。

支援兩種格式（自動偵測）：

    格式 A（無 header）：
        YYYYMMDD, HHMM, open, high, low, close
        時間欄為整數，無零填補（例：901 = 09:01，1500 = 15:00）

    格式 B（有 header，v1.1 規格）：
        Date, Time, Open, High, Low, Close
        Date + Time 合併為 datetime（YYYY-MM-DD HH:MM:SS）

輸出：
    DatetimeIndex（tz-naive，Asia/Taipei 本地時間），
    欄位：open / high / low / close（float64）
    已過濾交易時段外的 bar。
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

_COLS_A = ["date", "time", "open", "high", "low", "close"]


# ── 格式偵測 ─────────────────────────────────────────────────────────────────

def _detect_format(path: Path) -> str:
    """讀取第一行，判斷格式 A 或 B。

    Returns:
        "A" — 無 header，YYYYMMDD+HHMM 格式
        "B" — 有 header，Date+Time 欄位格式
    """
    with open(path, encoding="utf-8", errors="replace") as f:
        first_line = f.readline().strip()
    # 格式 B：第一欄 header 以 "date" 開頭（不分大小寫，去除 CSV 引號）
    first_token = first_line.split(",")[0].strip().strip('"').strip("'").lower()
    if first_token == "date":
        return "B"
    return "A"


# ── 單一 CSV 載入 ─────────────────────────────────────────────────────────────

def load_csv(path: str | Path) -> pd.DataFrame:
    """讀取單一 CSV/TXT 分K檔，回傳標準化 DataFrame。

    自動偵測格式 A（YYYYMMDD/HHMM，無 header）或
    格式 B（Date/Time 欄，有 header）。

    Returns:
        DatetimeIndex（tz-naive，Asia/Taipei 本地時間），
        欄位：open / high / low / close（float64），
        已過濾交易時段外的 bar。
    """
    path = Path(path)
    logger.info("載入：%s", path.name)

    fmt = _detect_format(path)
    logger.debug("偵測格式：%s (%s)", fmt, path.name)

    if fmt == "A":
        df = _load_format_a(path)
    else:
        df = _load_format_b(path)

    # 移除無效 bar（NaN 或 OHLC 邏輯錯誤）
    before = len(df)
    df = df.dropna(subset=["open", "high", "low", "close"])
    df = df[(df["high"] >= df["low"]) & (df["open"] > 0)]
    if (dropped := before - len(df)):
        logger.warning("移除 %d 根無效 bar", dropped)

    # 過濾交易時段
    df = filter_trading_sessions(df)

    logger.info(
        "載入完成：%d 根 bar（%s ~ %s）",
        len(df), df.index[0], df.index[-1],
    )
    return df[["open", "high", "low", "close"]]


def _load_format_a(path: Path) -> pd.DataFrame:
    """格式 A：YYYYMMDD, HHMM, open, high, low, close（無 header，無成交量）"""
    df = pd.read_csv(path, header=None, names=_COLS_A)

    # 合併 date + time → datetime
    # time 無補零，例：901 → "0901", 100 → "0100"
    df["datetime"] = pd.to_datetime(
        df["date"].astype(str) + df["time"].astype(str).str.zfill(4),
        format="%Y%m%d%H%M",
    )
    df = df.set_index("datetime").drop(columns=["date", "time"]).sort_index()

    for col in ("open", "high", "low", "close"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


def _load_format_b(path: Path) -> pd.DataFrame:
    """格式 B：Date, Time, Open, High, Low, Close（有 header，v1.1 規格）

    支援：
        Date="2022-01-03", Time="09:01:00"
        或合併欄 Date="2022-01-03 09:01:00"（Time 欄可無）
    """
    df = pd.read_csv(path)
    df.columns = [c.strip() for c in df.columns]  # 去除空白

    # 嘗試合併 Date + Time
    if "Time" in df.columns:
        dt_str = df["Date"].astype(str) + " " + df["Time"].astype(str)
    else:
        dt_str = df["Date"].astype(str)

    df["datetime"] = pd.to_datetime(dt_str, format="mixed")
    df = df.set_index("datetime").sort_index()

    # 標準化欄位名稱為小寫
    rename_map = {c: c.lower() for c in df.columns if c in ("Open", "High", "Low", "Close")}
    df = df.rename(columns=rename_map)

    for col in ("open", "high", "low", "close"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # 移除成交量為 0 的無效 bar（v1.1 規格要求）
    vol_col = next((c for c in df.columns if c.lower() == "volume"), None)
    if vol_col:
        before = len(df)
        df = df[df[vol_col] != 0]
        if (dropped := before - len(df)):
            logger.debug("移除 %d 根成交量為 0 的 bar", dropped)

    return df[["open", "high", "low", "close"]]


# ── 批次載入 ──────────────────────────────────────────────────────────────────

def load_csv_dir(csv_dir: str | Path) -> dict[str, pd.DataFrame]:
    """讀取目錄下所有 CSV/TXT，回傳 {filename_stem: DataFrame}。"""
    csv_dir = Path(csv_dir)
    results: dict[str, pd.DataFrame] = {}

    files = sorted(csv_dir.glob("*.csv")) + sorted(csv_dir.glob("*.txt"))
    if not files:
        logger.warning("目錄 %s 沒有找到 CSV/TXT 檔案", csv_dir)
        return results

    for f in files:
        try:
            results[f.stem] = load_csv(f)
        except Exception as exc:
            logger.error("跳過 %s：%s", f.name, exc)

    total = sum(len(d) for d in results.values())
    logger.info("載入完成：%d 個檔案，共 %d 根 bar", len(results), total)
    return results


# ── 時段過濾 ──────────────────────────────────────────────────────────────────

def filter_trading_sessions(df: pd.DataFrame) -> pd.DataFrame:
    """只保留台指期交易時段內的 bar。

    日盤：08:45 ~ 13:44（含）
    夜盤：15:00 ~ 次日 04:59（含）
    """
    t = df.index.time
    day   = (t >= pd.Timestamp("08:45").time()) & (t < pd.Timestamp("13:45").time())
    night = (t >= pd.Timestamp("15:00").time()) | (t < pd.Timestamp("05:00").time())
    mask  = day | night
    if (dropped := (~mask).sum()):
        logger.debug("過濾 %d 根非交易時段 bar", dropped)
    return df[mask]


def merge_dataframes(dfs: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """合併多個 DataFrame，去重並排序。"""
    if not dfs:
        raise ValueError("無可合併的 DataFrame")
    combined = pd.concat(list(dfs.values())).sort_index()
    combined = combined[~combined.index.duplicated(keep="last")]
    return combined
