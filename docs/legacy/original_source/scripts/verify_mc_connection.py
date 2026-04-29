"""MC12 連動驗證腳本 — 確認 AI 產出策略可與 MultiCharts 12 整合。

使用方式：
    # 一般診斷（不需 Admin）
    python scripts/verify_mc_connection.py

    # 含一次性設定（需管理員身分，僅首次執行）
    python scripts/verify_mc_connection.py --setup

    # 指定特定策略的 EL 檔
    python scripts/verify_mc_connection.py --el src/strategies/generated/macd_rsi_trend.el

驗證項目：
    [1] pywin32 / pywinauto / psutil 已安裝
    [2] MC12 安裝目錄存在
    [3] PLEditor.exe 存在
    [4] EL Studies 目錄可寫入
    [5] AI_TEST 已編譯（DLL 存在）
    [6] MC12 目前正在執行
    [7] EL 檔案語法基本驗證
    [8] （--setup 模式）執行一次性匯入並編譯

流程說明：
    首次使用需以 Admin 執行 --setup：
        1. 啟動 PLEditor
        2. File → Import EL Studies → AI_TEST.el
        3. Ctrl+F7 Build All
        4. 確認 DLL 生成

    之後每次 run_mc_backtest()：
        1. 覆寫 AI_TEST.el（含新策略邏輯）
        2. PLEditor Ctrl+F7 重新編譯（不需 Import）
        3. MC12 圖表觸發回測
        4. 解析 SPR CSV → MCBacktestResult
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

# ── 將專案根目錄加入 sys.path ─────────────────────────────────────
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

# 載入 .env（若存在）
_env_path = _ROOT / ".env"
if _env_path.exists():
    for _line in _env_path.read_text(encoding="utf-8").splitlines():
        if "=" in _line and not _line.startswith("#"):
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())


# ── 顏色輸出（Windows Console）────────────────────────────────────
def _green(s): return f"\033[92m{s}\033[0m"
def _red(s):   return f"\033[91m{s}\033[0m"
def _yellow(s): return f"\033[93m{s}\033[0m"
def _bold(s):  return f"\033[1m{s}\033[0m"


PASS = _green("[PASS]")
FAIL = _red("[FAIL]")
WARN = _yellow("[WARN]")
SKIP = _yellow("[SKIP]")
INFO = "      "


def check(label: str, ok: bool, detail: str = "", warn_only: bool = False) -> bool:
    """回傳 True 代表「不阻塞後續流程」。warn_only 項目即使失敗也回傳 True。"""
    tag = PASS if ok else (WARN if warn_only else FAIL)
    print(f"  {tag}  {label}")
    if detail:
        # 多行 detail：每行縮排對齊
        for line in detail.splitlines():
            print(f"  {INFO}  {line}")
    # warn_only 項目：警告但不算失敗
    return True if warn_only else ok


# ══════════════════════════════════════════════════════════════════
# 驗證函式
# ══════════════════════════════════════════════════════════════════

def check_dependencies() -> bool:
    """[1] 確認 pywin32 / pywinauto / psutil 已安裝。"""
    missing = []
    try:
        import win32gui  # noqa: F401
    except ImportError:
        missing.append("pywin32")
    try:
        import pywinauto  # noqa: F401
    except ImportError:
        missing.append("pywinauto")
    try:
        import psutil  # noqa: F401
    except ImportError:
        missing.append("psutil")

    if missing:
        return check(
            "依賴套件（pywin32 / pywinauto / psutil）",
            False,
            f"缺少：{', '.join(missing)}  →  pip install {' '.join(missing)}",
        )
    return check("依賴套件（pywin32 / pywinauto / psutil）", True)


def check_mc_install(mc_dir: str) -> bool:
    """[2] MC12 安裝目錄存在。"""
    p = Path(mc_dir)
    ok = p.exists()
    return check(
        f"MC12 安裝目錄  {mc_dir}",
        ok,
        "" if ok else "目錄不存在，請確認 config.yaml mc_bridge.mc_dir",
    )


def check_pleditor(mc_dir: str) -> bool:
    """[3] PLEditor.exe 存在。"""
    p = Path(mc_dir) / "PLEditor.exe"
    return check(
        "PLEditor.exe",
        p.exists(),
        str(p) if not p.exists() else "",
    )


def check_studies_dir(studies_dir: str) -> bool:
    """[4] EL Studies 目錄可寫入。"""
    p = Path(studies_dir)
    if not p.exists():
        try:
            p.mkdir(parents=True, exist_ok=True)
            writable = True
        except PermissionError:
            writable = False
    else:
        # 嘗試寫入測試檔
        test_file = p / "_aismart_write_test.tmp"
        try:
            test_file.write_text("test", encoding="utf-8")
            test_file.unlink()
            writable = True
        except PermissionError:
            writable = False

    return check(
        f"Studies 目錄可寫入  {studies_dir}",
        writable,
        "" if writable else "權限不足，請以管理員身分執行或調整目錄權限",
    )


def check_aismart_dll(mc_cfg: dict) -> bool:
    """[5] AI_TEST DLL 存在（已編譯）。"""
    from src.core.backtest.mc_bridge import _is_strategy_registered
    strategy_name = mc_cfg.get("strategy_name", "AI_TEST")
    registered = _is_strategy_registered(strategy_name, mc_cfg)
    dll_dir = (
        Path(mc_cfg.get("studies_dir", ""))
        .parents[1] / "Dlls" / "Strategies"
    )
    detail = (
        "" if registered
        else (
            f"DLL 未找到（目錄：{dll_dir}）\n"
            f"  {INFO}  解決方式：以管理員身分執行：\n"
            f"  {INFO}    python scripts/verify_mc_connection.py --setup"
        )
    )
    return check(
        f"AI_TEST 已編譯（DLL 存在）",
        registered,
        detail,
        warn_only=True,  # 未編譯只警告，不影響整體
    )


def check_mc_running() -> bool:
    """[6] MultiCharts64 目前正在執行。"""
    try:
        import psutil
        running = any(
            "multicharts" in (p.name() or "").lower()
            for p in psutil.process_iter(["name"])
        )
    except ImportError:
        running = False

    return check(
        "MultiCharts64 正在執行",
        running,
        "" if running else "請先開啟 MultiCharts 12",
        warn_only=True,
    )


def check_mc_window_connect() -> bool:
    """[6b] pywinauto 可連線到 MC12 主視窗。"""
    try:
        from pywinauto.findwindows import find_windows as _fw
        handles = _fw(class_name="ATL_MCMDIMainFrame")
        if handles:
            import win32gui
            title = win32gui.GetWindowText(handles[0])
            return check(
                "pywinauto 連線 MC12 主視窗",
                True,
                f"hwnd={handles[0]}  title='{title}'",
            )
        else:
            return check(
                "pywinauto 連線 MC12 主視窗",
                False,
                "找不到 ATL_MCMDIMainFrame 視窗，請先開啟 MultiCharts 12",
                warn_only=True,
            )
    except ImportError:
        return check(
            "pywinauto 連線 MC12 主視窗",
            False,
            "pywinauto 未安裝",
            warn_only=True,
        )
    except Exception as exc:
        return check(
            "pywinauto 連線 MC12 主視窗",
            False,
            str(exc),
            warn_only=True,
        )


def check_el_syntax(el_path: Path) -> bool:
    """[7] EL 檔案基本語法驗證。"""
    if not el_path.exists():
        return check(
            f"EL 檔案存在  {el_path.name}",
            False,
            f"找不到：{el_path}",
        )

    el_code = el_path.read_text(encoding="utf-8")

    # 基本必要關鍵字
    required = {
        "inputs":    "inputs 宣告",
        "variables": "variables 宣告",
        "Buy":       "Buy 進場指令",
        "Sell":      "Sell Short 進場指令",
    }
    missing_keys = [label for kw, label in required.items() if kw.lower() not in el_code.lower()]

    if missing_keys:
        return check(
            f"EL 語法驗證  {el_path.name}",
            False,
            f"缺少：{', '.join(missing_keys)}",
        )

    lines = [l for l in el_code.splitlines() if l.strip() and not l.strip().startswith("//")]
    return check(
        f"EL 語法驗證  {el_path.name}",
        True,
        f"{len(lines)} 行有效程式碼",
    )


def check_el_to_mc_writeable(el_path: Path, mc_cfg: dict) -> bool:
    """[7b] 確認可將 EL 內容寫入 MC12 的 Studies 目錄（s_AI_TEST.el）。"""
    from src.core.backtest.mc_bridge import _strategy_el_filename, _clean_el_for_pleditor
    studies_dir = mc_cfg.get("studies_dir", "")
    strategy_name = mc_cfg.get("strategy_name", "AI_TEST")
    filename = _strategy_el_filename(strategy_name)
    target = Path(studies_dir) / filename

    try:
        el_code = el_path.read_text(encoding="utf-8")
        clean = _clean_el_for_pleditor(el_code)
        target.write_bytes(clean.encode("cp950", errors="replace"))
        return check(
            f"EL 寫入 MC12 Studies 目錄",
            True,
            f"已寫入：{target}",
        )
    except Exception as exc:
        return check(
            f"EL 寫入 MC12 Studies 目錄",
            False,
            str(exc),
        )


# ══════════════════════════════════════════════════════════════════
# 一次性設定（--setup 模式）
# ══════════════════════════════════════════════════════════════════

def run_setup(mc_cfg: dict, el_path: Path | None) -> None:
    """以 Admin 執行一次性 AI_TEST 匯入與編譯。"""
    from src.core.backtest.mc_bridge import _is_admin, setup_aismart_template

    if not _is_admin():
        print()
        print(_red("  [ERROR]  --setup 需要管理員身分！"))
        print("  請在「命令提示字元（系統管理員）」中執行：")
        print("    python scripts/verify_mc_connection.py --setup")
        sys.exit(1)

    el_code = None
    if el_path and el_path.exists():
        el_code = el_path.read_text(encoding="utf-8")
        print(f"  使用 EL 檔案：{el_path}")
    else:
        print("  使用預設 MA Cross 模板")

    print()
    print("  正在執行一次性設定（PLEditor 匯入 + Build All）...")
    try:
        setup_aismart_template(el_code=el_code)
        print(_green("  [PASS]  一次性設定完成！AI_TEST 已在 MC12 中註冊。"))
    except Exception as exc:
        print(_red(f"  [FAIL]  設定失敗：{exc}"))
        sys.exit(1)


# ══════════════════════════════════════════════════════════════════
# 端對端流程展示
# ══════════════════════════════════════════════════════════════════

def show_e2e_flow(el_path: Path, mc_cfg: dict) -> None:
    """展示完整的 AI策略 → MC12 端對端流程。"""
    print()
    print(_bold("══ 端對端流程（AI 生成策略 → MC12 精測）══"))
    print()
    print("  Step 1  AI 生成 Python 策略")
    print("          src/strategies/generated/macd_rsi_trend.py")
    print()
    print("  Step 2  自動轉換為 EasyLanguage")
    print(f"          {el_path}")
    print()
    print("  Step 3  寫入 MC12 Studies 目錄")
    print(f"          {mc_cfg.get('studies_dir')}/AI_TEST.el")
    print()
    print("  Step 4  PLEditor.exe Ctrl+F7 Build All（重新編譯）")
    print()
    print("  Step 5  MultiCharts 圖表觸發回測")
    print(f"          圖表標題含：{mc_cfg.get('chart_title', 'AISMART')}")
    print()
    print("  Step 6  解析 Strategy Performance Report CSV")
    print(f"          reports/mc_spr/spr_MACD_RSI_Trend_<timestamp>.csv")
    print()
    print("  Step 7  回傳 MCBacktestResult")
    print("          Sharpe / MaxDD / PF / Trades / WinRate / OOS_IS")
    print()
    print(_bold("  呼叫範例："))
    print("""
    from pathlib import Path
    from src.core.backtest.mc_bridge import run_mc_backtest

    el_code = Path("src/strategies/generated/macd_rsi_trend.el").read_text(encoding="utf-8")
    result = run_mc_backtest(
        strategy_id="MACD_RSI_Trend",
        el_code=el_code,
    )
    print(result.summary())
    """)


# ══════════════════════════════════════════════════════════════════
# 主程式
# ══════════════════════════════════════════════════════════════════

def main() -> int:
    parser = argparse.ArgumentParser(description="MC12 連動驗證工具")
    parser.add_argument(
        "--setup", action="store_true",
        help="執行一次性設定（需管理員身分）",
    )
    parser.add_argument(
        "--el", default=None,
        help="指定 EL 檔路徑（預設：src/strategies/generated/macd_rsi_trend.el）",
    )
    args = parser.parse_args()

    # 啟用 ANSI 顏色（Windows 10+）
    os.system("")

    print()
    print(_bold("═══════════════════════════════════════════════════"))
    print(_bold("  AISMART × MultiCharts 12  連動驗證"))
    print(_bold("═══════════════════════════════════════════════════"))
    print()

    # ── 載入設定 ────────────────────────────────────────────────
    from src.core.backtest.mc_bridge import _load_mc_config, _is_admin
    mc_cfg = _load_mc_config()
    mc_dir = mc_cfg.get("mc_dir", r"C:\Program Files\TS Support\MultiCharts64")

    el_path = Path(args.el) if args.el else _ROOT / "src/strategies/generated/macd_rsi_trend.el"

    print(f"  MC12 目錄：{mc_dir}")
    print(f"  EL 檔案 ：{el_path}")
    print(f"  Admin  ：{'Yes' if _is_admin() else 'No (部分操作需要)'}")
    print()

    # ── 執行設定模式 ─────────────────────────────────────────────
    if args.setup:
        run_setup(mc_cfg, el_path)
        print()

    # ── 執行診斷檢查 ─────────────────────────────────────────────
    print(_bold("── 診斷檢查 ─────────────────────────────────────────"))
    results = []

    results.append(check_dependencies())
    results.append(check_mc_install(mc_dir))
    results.append(check_pleditor(mc_dir))
    results.append(check_studies_dir(mc_cfg.get("studies_dir", "")))
    results.append(check_aismart_dll(mc_cfg))
    results.append(check_mc_running())
    results.append(check_mc_window_connect())
    results.append(check_el_syntax(el_path))

    if el_path.exists():
        results.append(check_el_to_mc_writeable(el_path, mc_cfg))

    # ── 判斷整體狀態 ─────────────────────────────────────────────
    print()
    passed = sum(1 for r in results if r)
    total  = len(results)

    if passed == total:
        print(_green(f"  [OK]  全部 {total} 項通過！AI 策略可完整連動 MC12。"))
        status = 0
    elif passed >= total - 2:
        print(_yellow(f"  [!]   {passed}/{total} 通過，存在警告。"))
        print("       主要功能可用，但建議解決上方警告項目。")
        status = 0
    else:
        print(_red(f"  [X]  {passed}/{total} 通過，存在阻塞性問題。"))
        print("       請解決上方 [FAIL] 項目後重新執行。")
        status = 1

    # ── 展示端對端流程 ───────────────────────────────────────────
    show_e2e_flow(el_path, mc_cfg)

    # ── 若 AI_TEST 未編譯，給出清晰指示 ─────────────────────
    from src.core.backtest.mc_bridge import _is_strategy_registered
    if not _is_strategy_registered(mc_cfg.get("strategy_name", "AI_TEST"), mc_cfg):
        print(_yellow("  [!]  下一步：以管理員身分執行一次性設定"))
        print()
        print("    1. 右鍵點擊「命令提示字元」→「以系統管理員身分執行」")
        print(f"    2. cd {_ROOT}")
        print("    3. python scripts/verify_mc_connection.py --setup")
        print()
        print("    設定完成後 AI_TEST.dll 會出現在 MC12 中，")
        print("    之後即可呼叫 run_mc_backtest() 執行精測回測。")
        print()

    return status


if __name__ == "__main__":
    sys.exit(main())
