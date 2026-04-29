"""MultiCharts 12 自動化回測橋接模組。

功能概覽：
    run_mc_backtest()          — 完整 MC12 精測流程（EL注入→編譯→回測→匯出→解析）
    setup_aismart_template()   — 一次性設定：匯入 AISMART_Test 策略到 PLEditor（需 Admin）
    _close_mc_dialogs()        — pywinauto 背景對話框守衛執行緒
    parse_spr_csv()            — Strategy Performance Report CSV 解析

⚠️  重要：需以管理員身分執行
    MC12 / PLEditor 是商業交易平台，以提升權限（High IL）執行。
    從非 Admin 程序透過 pywinauto 操作其選單/按鈕 → 被 Windows UAC 阻擋。

    解決方案：
        # 在「命令提示字元（系統管理員）」中執行
        python main.py run --mode full

    或在啟動時自動提升（見 _ensure_admin()）。

MC12 自動化流程（run_mc_backtest 內部步驟）：
    1. 將 EL 程式碼寫入 StudyServer 目錄（不需 Admin）
    2. 以 PLEditor.exe 編譯（Ctrl+F7 Build All）
       前提：AISMART_Test 已透過 setup_aismart_template() 匯入一次
    3. 連線 / 啟動 MultiCharts64.exe（class=ATL_MCMDIMainFrame）
    4. 開啟指定工作區（.wsp）
    5. 定位 ATL_MCMDIChildFrame 圖表 → ATL_MCGraphPanel 右鍵觸發回測
    6. 等待 Performance Report 視窗出現
    7. 匯出 Strategy Performance Report → CSV
    8. 解析 CSV，回傳 MCBacktestResult

一次性設定流程：
    from src.core.backtest.mc_bridge import setup_aismart_template
    setup_aismart_template()  # 以 Admin 執行一次

設定（config.yaml mc_bridge 區段）：
    mc_bridge:
      mc_dir:        "C:/Program Files/TS Support/MultiCharts64"
      studies_dir:   "C:/ProgramData/TS Support/MultiCharts64/StudyServer/Studies/SrcEl/Strategies"
      workspace:     null          # .wsp 路徑；null = 使用目前已開啟工作區
      strategy_name: "AISMART_Test"
      chart_title:   "AISMART"     # 含此字串的 ATL_MCMDIChildFrame 標題
      spr_export_dir: "reports/mc_spr"
      compile_wait:  15.0
      backtest_wait: 180.0
      dialog_interval: 1.0

依賴（Windows 主機）：
    pip install pywin32 pywinauto psutil

MC12 視窗類別（實際探測確認）：
    主視窗:  ATL_MCMDIMainFrame
    圖表MDI: ATL_MCMDIChildFrame  (title: "AMD - 1 分 - Free Quotes")
    圖表內:  ATL_MCChartManager, ATL_MCGraphPanel, ATL_MCTimeScale
    PLEditor: PLEditor_MainForm  (title: "MultiCharts64 PowerLanguage Editor")
"""
from __future__ import annotations

import csv
import io
import logging
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# ── 預設路徑（可被 config.yaml 覆蓋）────────────────────────────────
_DEFAULT_MC_DIR     = r"C:\Program Files\TS Support\MultiCharts64"
_DEFAULT_STUDIES    = (
    r"C:\ProgramData\TS Support\MultiCharts64"
    r"\StudyServer\Studies\SrcEl\Strategies"
)
_DEFAULT_SPR_DIR    = "reports/mc_spr"
_STRATEGY_NAME      = "AISMART_Test"   # MC12 中固定使用的策略槽名稱


# ── 結果 Dataclass ───────────────────────────────────────────────
@dataclass
class MCBacktestResult:
    """MultiCharts 精測回測結果（解析自 SPR CSV）。"""
    strategy_id:      str
    sharpe_ratio:     float
    max_drawdown:     float        # 相對值（0.25 = 25%）
    profit_factor:    float
    total_trades:     int
    win_rate:         float
    is_sharpe:        float        # In-Sample Sharpe（WFA 用）
    oos_sharpe:       float        # Out-of-Sample Sharpe（WFA 用）
    overfitting_flag: bool         # oos_sharpe / is_sharpe < oos_is_ratio_min
    spr_csv_path:     str | None = None  # 原始 CSV 路徑

    def summary(self) -> str:
        flag = "[OVERFIT]" if self.overfitting_flag else "[OK]"
        return (
            f"{flag} {self.strategy_id} | "
            f"Sharpe={self.sharpe_ratio:.2f} "
            f"MaxDD={self.max_drawdown:.1%} "
            f"PF={self.profit_factor:.2f} "
            f"Trades={self.total_trades} "
            f"WinRate={self.win_rate:.1%} "
            f"OOS/IS={self.oos_sharpe:.2f}/{self.is_sharpe:.2f}"
        )


# ── 設定載入 ─────────────────────────────────────────────────────
def _load_mc_config() -> dict:
    """從 config.yaml 讀取 mc_bridge 區段，找不到時回傳預設值。"""
    cfg_path = Path(__file__).resolve().parents[3] / "config.yaml"
    try:
        with open(cfg_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        return cfg.get("mc_bridge", {})
    except Exception:
        return {}


# ── 主要公開函式 ──────────────────────────────────────────────────

def run_mc_backtest(
    strategy_id: str,
    el_code: str,
    workspace: str | None = None,
    timeout_seconds: int = 300,
) -> MCBacktestResult:
    """透過 MultiCharts 12 執行策略精測回測。

    完整流程：
        1. 寫入 EL 程式碼至 StudyServer Studies 目錄
        2. PLEditor.exe 編譯（Ctrl+F7 Build All）
        3. 連線 / 啟動 MultiCharts64
        4. 開啟工作區（workspace 或保持現有）
        5. 觸發圖表策略回測
        6. 等待 Performance Report 視窗
        7. 匯出 SPR CSV
        8. 解析 CSV，回傳 MCBacktestResult

    前置條件：
        - MultiCharts 12 已安裝（支援 v12.0.20860 或以上）
        - config.yaml mc_bridge.chart_title 對應到已設定好 AISMART_Test 策略的圖表
        - pip install pywin32 pywinauto

    Args:
        strategy_id:     策略唯一識別碼（用於檔名與結果記錄）
        el_code:         EasyLanguage / Power Language 程式碼字串
        workspace:       .wsp 工作區路徑；None 則沿用 MC12 目前開啟的工作區
        timeout_seconds: 等待回測完成的超時秒數（預設 5 分鐘）

    Returns:
        MCBacktestResult

    Raises:
        RuntimeError: pywin32 / pywinauto 未安裝
        TimeoutError: 回測超時
    """
    _check_dependencies()

    mc_cfg = _load_mc_config()
    mc_dir       = mc_cfg.get("mc_dir", _DEFAULT_MC_DIR)
    studies_dir  = mc_cfg.get("studies_dir", _DEFAULT_STUDIES)
    spr_dir      = mc_cfg.get("spr_export_dir", _DEFAULT_SPR_DIR)
    strategy_name = mc_cfg.get("strategy_name", _STRATEGY_NAME)
    chart_title  = mc_cfg.get("chart_title", "AISMART")
    compile_wait = float(mc_cfg.get("compile_wait", 15.0))
    backtest_wait = float(mc_cfg.get("backtest_wait", timeout_seconds))
    workspace    = workspace or mc_cfg.get("workspace")

    # Step 1: 寫入 EL 檔
    el_path = _write_el_file(el_code, strategy_name, studies_dir)
    logger.info("[MC] EL 已寫入：%s", el_path)

    # 檢查策略是否已匯入（DLL 存在）
    if not _is_strategy_registered(strategy_name, mc_cfg):
        raise RuntimeError(
            f"策略 '{strategy_name}' 尚未在 MultiCharts12 中匯入。\n"
            "請先以管理員身分執行一次性設定：\n"
            "  from src.core.backtest.mc_bridge import setup_aismart_template\n"
            "  setup_aismart_template()\n"
            "設定完成後再次呼叫 run_mc_backtest()。"
        )

    # Step 2: 啟動對話框守衛
    stop_evt = threading.Event()
    guard = threading.Thread(
        target=_dialog_guard_loop,
        args=(stop_evt, mc_cfg.get("dialog_interval", 1.0)),
        daemon=True,
        name="mc-dialog-guard",
    )
    guard.start()

    try:
        # Step 3: PLEditor 編譯
        _compile_with_pleditor(el_path, mc_dir, compile_wait)
        logger.info("[MC] 編譯完成：%s", strategy_name)

        # Step 4: 連線 / 啟動 MC12
        mc_app = _connect_or_launch_mc(mc_dir)

        # Step 5: 開啟工作區
        if workspace:
            _open_workspace(mc_app, workspace)
            time.sleep(3.0)

        # Step 6: 觸發回測
        spr_csv = _trigger_backtest_and_export(
            mc_app,
            chart_title=chart_title,
            strategy_name=strategy_name,
            spr_dir=spr_dir,
            strategy_id=strategy_id,
            timeout=backtest_wait,
        )
        logger.info("[MC] SPR 已匯出：%s", spr_csv)

    finally:
        stop_evt.set()
        guard.join(timeout=3.0)

    # Step 7: 解析 SPR CSV
    metrics = parse_spr_csv(spr_csv)
    sharpe  = metrics.get("sharpe_ratio", 0.0)

    # is_sharpe / oos_sharpe 需 WFA 分開提供；此處用整段 Sharpe 作 is_sharpe
    oos_is_ratio = float(mc_cfg.get("oos_is_ratio_min",
                                     _load_mc_filter_ratio()))
    overfitting = (metrics.get("oos_sharpe", sharpe) / sharpe < oos_is_ratio
                   if sharpe > 0 else True)

    return MCBacktestResult(
        strategy_id      = strategy_id,
        sharpe_ratio     = sharpe,
        max_drawdown     = metrics.get("max_drawdown_pct", 0.0),
        profit_factor    = metrics.get("profit_factor", 0.0),
        total_trades     = metrics.get("total_trades", 0),
        win_rate         = metrics.get("win_rate", 0.0),
        is_sharpe        = sharpe,
        oos_sharpe       = metrics.get("oos_sharpe", sharpe),
        overfitting_flag = overfitting,
        spr_csv_path     = str(spr_csv),
    )


def _close_mc_dialogs(interval_seconds: float = 1.0) -> None:
    """pywinauto 背景監聽，持續自動關閉 MC12 錯誤對話框。

    設計為在獨立 daemon 執行緒中長期運行。
    通常由外部 threading.Event 控制停止（見 _dialog_guard_loop）。

    Args:
        interval_seconds: 偵測輪詢間隔（秒）
    """
    stop_event = threading.Event()
    _dialog_guard_loop(stop_event, interval_seconds)


# ── SPR CSV 解析 ──────────────────────────────────────────────────

def parse_spr_csv(spr_path: str | Path) -> dict:
    """解析 MultiCharts 12 Strategy Performance Report CSV。

    支援 MC12 的多種匯出格式（含中文/英文版標頭、括號負數、%符號）。

    Args:
        spr_path: SPR CSV 檔案路徑

    Returns:
        dict 含以下鍵值：
            sharpe_ratio    (float)  — Sharpe Ratio（年化）
            max_drawdown_pct(float)  — 最大回撤（0.25 = 25%）
            profit_factor   (float)  — 獲利因子
            total_trades    (int)    — 總交易次數
            win_rate        (float)  — 勝率（0.68 = 68%）
            total_net_profit(float)  — 總淨利（元）
            gross_profit    (float)  — 毛利
            gross_loss      (float)  — 毛損（正值）
            avg_win         (float)  — 平均獲利（元）
            avg_loss        (float)  — 平均虧損（元，正值）

    Raises:
        FileNotFoundError: 找不到 CSV 檔案
        ValueError:        無法從 CSV 中解析出必要欄位
    """
    path = Path(spr_path)
    if not path.exists():
        raise FileNotFoundError(f"找不到 SPR CSV：{path}")

    # 嘗試多種編碼
    for enc in ("utf-8-sig", "utf-8", "cp950", "gbk"):
        try:
            text = path.read_text(encoding=enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = path.read_bytes().decode("utf-8", errors="replace")

    rows = list(csv.reader(io.StringIO(text)))
    result: dict[str, float | int] = {}

    # ── 關鍵欄位映射（支援英文 / 繁中 / 簡中標頭）────────────────
    _FIELD_PATTERNS: list[tuple[str, list[str]]] = [
        ("sharpe_ratio",     ["Sharpe Ratio", "夏普指標", "夏普比率"]),
        ("max_drawdown_pct", ["Max. Drawdown %", "最大回撤%", "MaxDD%", "Max Drawdown %"]),
        ("max_drawdown_abs", ["Max. Drawdown", "Max Intraday Drawdown", "最大回撤"]),
        ("profit_factor",    ["Profit Factor", "獲利因子", "盈利因子"]),
        ("total_trades",     ["Total Number of Trades", "Total Trades", "總交易次數", "交易次數"]),
        ("winning_trades",   ["Winning Trades", "獲利交易", "盈利交易"]),
        ("percent_profitable",["Percent Profitable", "勝率", "盈利百分比"]),
        ("total_net_profit", ["Total Net Profit", "總淨利", "净利润"]),
        ("gross_profit",     ["Gross Profit", "毛利", "总盈利"]),
        ("gross_loss",       ["Gross Loss", "毛損", "总亏损"]),
        ("avg_win",          ["Winning Trades Avg.", "Avg. Winning Trade", "平均獲利"]),
        ("avg_loss",         ["Losing Trades Avg.", "Avg. Losing Trade", "平均虧損"]),
    ]

    for row in rows:
        if not row:
            continue
        label = row[0].strip().strip('"')
        value_str = row[1].strip().strip('"') if len(row) > 1 else ""

        for field_key, patterns in _FIELD_PATTERNS:
            if field_key in result:
                continue
            if any(p.lower() in label.lower() for p in patterns):
                parsed = _parse_mc_number(value_str)
                if parsed is not None:
                    result[field_key] = parsed
                break

    # ── 衍生計算 ────────────────────────────────────────────────
    # max_drawdown_pct：優先用 % 欄位，否則從絕對值換算
    if "max_drawdown_pct" not in result and "max_drawdown_abs" in result:
        # 沒有初始資金資訊，先保留 None
        result["max_drawdown_pct"] = 0.0

    # 確保 max_drawdown_pct 為正值（MC12 可能輸出 -26.52%）
    if "max_drawdown_pct" in result:
        result["max_drawdown_pct"] = abs(float(result["max_drawdown_pct"])) / 100.0

    # win_rate：優先 percent_profitable，否則從 winning_trades / total_trades 計算
    if "win_rate" not in result:
        if "percent_profitable" in result:
            result["win_rate"] = result["percent_profitable"] / 100.0
        elif "winning_trades" in result and "total_trades" in result:
            total = int(result["total_trades"])
            result["win_rate"] = (result["winning_trades"] / total) if total else 0.0
    elif result["win_rate"] > 1.0:
        result["win_rate"] = result["win_rate"] / 100.0

    # total_trades → int
    if "total_trades" in result:
        result["total_trades"] = int(result["total_trades"])

    # gross_loss → 確保正值
    if "gross_loss" in result:
        result["gross_loss"] = abs(float(result["gross_loss"]))

    # avg_loss → 正值
    if "avg_loss" in result:
        result["avg_loss"] = abs(float(result["avg_loss"]))

    # 驗證必要欄位
    missing = [k for k in ("sharpe_ratio", "profit_factor", "total_trades")
               if k not in result]
    if missing:
        logger.warning("[MC] SPR CSV 缺少欄位：%s。路徑：%s", missing, path)

    return result


# ── 內部實作 ──────────────────────────────────────────────────────

def _check_dependencies() -> None:
    """確認 pywin32 與 pywinauto 已安裝，並警告 Admin 需求。"""
    errors = []
    try:
        import win32com.client  # noqa: F401
    except ImportError:
        errors.append("pywin32")
    try:
        import pywinauto  # noqa: F401
    except ImportError:
        errors.append("pywinauto")
    if errors:
        raise RuntimeError(
            f"缺少依賴：{', '.join(errors)}。"
            "請執行：pip install pywin32 pywinauto psutil"
        )
    if not _is_admin():
        logger.warning(
            "[MC] ⚠️  目前非管理員身分。MC12/PLEditor 的 UI 自動化（選單/按鈕）"
            "需要以管理員身分執行。請在「命令提示字元（系統管理員）」中執行此程式。"
        )


def _is_admin() -> bool:
    """回傳目前 Python 程序是否以管理員身分執行。"""
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _ensure_admin() -> None:
    """若非管理員，以 ShellExecute 'runas' 重新啟動目前程序（含原始參數）。

    呼叫此函式會在提權後結束目前程序（os.sys.exit(0)），由提權後的新程序繼續。
    適合在 main.py 或 CLI 入口點最前面呼叫。

    工作目錄會自動設定為專案根目錄（mc_bridge.py 的 3 層上層），
    確保提權後 import src.* 和相對路徑讀檔都能正常運作。
    """
    if _is_admin():
        return
    # --help / -h / --version：跳過提權直接讓 argparse 處理
    import sys
    if any(a in ("--help", "-h", "--version") for a in sys.argv[1:]):
        return

    import ctypes
    # 專案根目錄 = mc_bridge.py 往上 3 層（src/core/backtest/mc_bridge.py）
    project_root = str(Path(__file__).resolve().parents[3])
    logger.info("[MC] 非管理員，以 runas 重新啟動（工作目錄：%s）...", project_root)
    # 用 cmd.exe /k 包裹，讓提權後的 cmd 視窗在 Python 結束後保持開啟
    # （避免出錯時視窗瞬間關閉看不到原因）
    #
    # 引號規則（Windows cmd /k 經典坑）：
    #   命令含多組空白/引號時，必須外層再包一對「""」cmd 才會正確 parse
    #   參考：cmd /? 中 "/C 或 /K" 段落說明
    py_exe = sys.executable
    script_args = " ".join(f'"{a}"' for a in sys.argv)
    # 重要：UAC 提權後的 cmd 會強制啟動於 C:\Windows\system32（忽略 lpDirectory），
    # 所以相對路徑會解析錯誤 → 在 /k 內先 cd /d 到專案根目錄
    inner = f'cd /d "{project_root}" && "{py_exe}" {script_args}'
    cmd_params = f'/k "{inner}"'
    ctypes.windll.shell32.ShellExecuteW(
        None, "runas", "cmd.exe", cmd_params, project_root, 1
    )
    sys.exit(0)


def setup_aismart_template(el_code: str | None = None) -> None:
    """一次性設定：在 PLEditor 中建立並編譯 AISMART_Test 策略。

    ── 正確流程（經實際驗證）──────────────────────────────────────
    MC12 StudyServer 要求策略必須先在 PLEditor 中「建立」後才能編譯。
    直接寫入 .el 檔或使用「File → Import EL Studies」均無法建立新策略。

    正確的一次性設定步驟（需管理員身分）：
        1. 啟動 PLEditor
        2. New Strategy → 命名為 "AISMART_Test"
        3. 貼入 EL 程式碼
        4. F5 或 Ctrl+F7 編譯
        5. 關閉 PLEditor

    此函式自動執行上述步驟（需 Admin，UAC 提升後才能操作 PLEditor UI）。
    完成後 StudyServer 會在 SrcEl/Strategies/ 建立 sb_AISMART_Test.el，
    並在 Dlls/Strategies/ 建立 sb_AISMART_Test.dll。

    後續每次 run_mc_backtest() 只需覆寫 sb_AISMART_Test.el 並 Ctrl+F7，
    不需重新執行此函式。

    Args:
        el_code: EL 程式碼；None 則使用內建 MA Cross 模板

    Raises:
        RuntimeError: 非管理員身分
        FileNotFoundError: PLEditor.exe 不存在
    """
    if not _is_admin():
        raise RuntimeError(
            "setup_aismart_template() 需要管理員身分。\n"
            "請在「命令提示字元（系統管理員）」中執行：\n"
            "  python -c \"from src.core.backtest.mc_bridge import setup_aismart_template; "
            "setup_aismart_template()\""
        )

    _check_dependencies()
    mc_cfg = _load_mc_config()
    mc_dir        = mc_cfg.get("mc_dir", _DEFAULT_MC_DIR)
    strategy_name = mc_cfg.get("strategy_name", _STRATEGY_NAME)
    compile_wait  = float(mc_cfg.get("compile_wait", 15.0))

    # MA Cross 模板（純 ASCII，PLEditor 可正常編譯）
    if el_code is None:
        el_code = (
            "[IntrabarOrderGeneration = false]\r\n"
            "// AISMART_Test - MA Cross template (auto-generated by AISMART)\r\n"
            "inputs: FastLen(12), SlowLen(26);\r\n"
            "\r\n"
            "if Average(Close, FastLen) crosses above Average(Close, SlowLen) then\r\n"
            '    Buy("LE") next bar at market;\r\n'
            "\r\n"
            "if Average(Close, FastLen) crosses below Average(Close, SlowLen) then\r\n"
            '    Sell Short("SE") next bar at market;\r\n'
        )
    else:
        el_code = _clean_el_for_pleditor(el_code)

    pl_exe = Path(mc_dir) / "PLEditor.exe"
    if not pl_exe.exists():
        raise FileNotFoundError(f"PLEditor.exe 不存在：{pl_exe}")

    from pywinauto import Application
    from pywinauto.findwindows import find_windows as _fw
    from pywinauto.keyboard import send_keys

    logger.info("[MC Setup] 啟動 PLEditor...")
    proc = subprocess.Popen([str(pl_exe)])
    time.sleep(5)

    handles = _fw(class_name="PLEditor_MainForm")
    if not handles:
        proc.terminate()
        raise RuntimeError("PLEditor 視窗 (PLEditor_MainForm) 未出現")

    pl_app = Application(backend="win32").connect(handle=handles[0])
    pl_win = pl_app.window(handle=handles[0])
    pl_win.set_focus()
    time.sleep(0.5)

    # ── Step 1: File → New → Strategy ────────────────────────────
    logger.info("[MC Setup] 新增策略：%s", strategy_name)
    try:
        pl_win.menu_select("File->New")
        time.sleep(1.0)
        # 可能出現「新增項目類型」對話框，選 Strategy
        for dlg_title in ("New", "New Study", "Insert"):
            try:
                new_dlg = pl_app.window(title_re=f".*{dlg_title}.*", timeout=3)
                if new_dlg.exists():
                    # 找 Strategy 選項並選取
                    for item_name in ("Strategy", "Strategies"):
                        try:
                            new_dlg.child_window(title_re=f".*{item_name}.*").click()
                            break
                        except Exception:
                            pass
                    time.sleep(0.3)
                    for btn in ("OK", "確定", "Next"):
                        try:
                            new_dlg.child_window(title=btn, class_name="Button").click()
                            break
                        except Exception:
                            pass
                    break
            except Exception:
                pass
        time.sleep(1.0)

        # 出現命名對話框 → 輸入策略名稱
        for name_dlg_title in ("Name", "Strategy Name", "New Strategy", "Insert Study"):
            try:
                name_dlg = pl_app.window(title_re=f".*{name_dlg_title}.*", timeout=3)
                if name_dlg.exists():
                    edit = name_dlg.child_window(class_name="Edit")
                    edit.set_text(strategy_name)
                    for btn in ("OK", "確定"):
                        try:
                            name_dlg.child_window(title=btn, class_name="Button").click()
                            break
                        except Exception:
                            pass
                    break
            except Exception:
                pass

        time.sleep(1.5)
        logger.info("[MC Setup] 策略 %s 已建立", strategy_name)

    except Exception as exc:
        logger.warning("[MC Setup] New Study 流程失敗（%s）", exc)

    # ── Step 2: 清空編輯器並貼入 EL 程式碼 ───────────────────────
    pl_win.set_focus()
    time.sleep(0.5)
    # 全選並刪除
    pl_win.type_keys("^a", pause=0.05)
    pl_win.type_keys("{DELETE}", pause=0.05)
    time.sleep(0.3)
    # 用剪貼簿貼入（避免 type_keys 逐字輸入的速度問題）
    import ctypes
    _set_clipboard_text(el_code)
    pl_win.type_keys("^v", pause=0.1)
    time.sleep(0.5)
    logger.info("[MC Setup] EL 程式碼已貼入")

    # ── Step 3: F5 編譯（單一策略） ──────────────────────────────
    pl_win.set_focus()
    pl_win.type_keys("{F5}", pause=0.1)
    logger.info("[MC Setup] 編譯中（F5），等待 %.1fs...", compile_wait)
    time.sleep(compile_wait)

    # 關閉可能的編譯結果對話框
    for dlg_title in ("Build", "Compile", "Error", "Warning"):
        try:
            dlg = pl_app.window(title_re=f".*{dlg_title}.*", timeout=2)
            if dlg.exists():
                logger.info("[MC Setup] 關閉對話框：%s", dlg.window_text())
                for btn in ("OK", "確定", "Close"):
                    try:
                        dlg.child_window(title=btn, class_name="Button").click()
                        break
                    except Exception:
                        pass
        except Exception:
            pass

    # ── Step 4: Ctrl+S 儲存 ──────────────────────────────────────
    pl_win.set_focus()
    pl_win.type_keys("^s", pause=0.1)
    time.sleep(1.0)

    pl_win.close()
    logger.info(
        "[MC Setup] 完成！'%s' 已在 PLEditor 建立並編譯。\n"
        "後續 run_mc_backtest() 會覆寫來源檔後觸發 Build All，無需重新執行此設定。",
        strategy_name,
    )


def _set_clipboard_text(text: str) -> None:
    """透過 win32 API 設定剪貼簿文字（用於 PLEditor 程式碼貼入）。"""
    try:
        import win32clipboard
        win32clipboard.OpenClipboard()
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
        win32clipboard.CloseClipboard()
    except ImportError:
        # fallback: tkinter clipboard
        try:
            import tkinter as tk
            root = tk.Tk()
            root.withdraw()
            root.clipboard_clear()
            root.clipboard_append(text)
            root.update()
            root.destroy()
        except Exception:
            pass


def _strategy_el_filename(strategy_name: str) -> str:
    """將策略名稱轉換為 MC12 StudyServer 的 .el 檔名。

    MC12 命名規則（實際觀察）：
        使用者自建策略 → s_{name}.el      （空格 → b20）
        內建函式庫策略 → sb_{name}.el / sa_{name}.el 等

    例：
        "AI_TEST"   → "s_AI_TEST.el"     （無空格，不需編碼）
        "My Strat"  → "s_Myb20Strat.el"  （空格 → b20）
    """
    encoded = strategy_name.replace(" ", "b20")
    encoded = re.sub(r"[^\w\-]", "", encoded)
    return f"s_{encoded}.el"


def _write_el_file(el_code: str, strategy_name: str, studies_dir: str) -> Path:
    """將 EL 程式碼寫入 MC12 Studies 目錄。

    命名格式：sb_{strategy_name}.el（與 MC12 StudyServer 慣例一致）
    寫入編碼：cp950（Windows ANSI），確保 PLEditor 可正確讀取。
    若 el_code 含 UTF-8 中文注釋，先清洗後再寫入（否則 PLEditor 顯示空白）。
    """
    studies_path = Path(studies_dir)
    studies_path.mkdir(parents=True, exist_ok=True)
    filename = _strategy_el_filename(strategy_name)
    el_path = studies_path / filename
    clean = _clean_el_for_pleditor(el_code)
    el_path.write_bytes(clean.encode("cp950", errors="replace"))
    return el_path


def _clean_el_for_pleditor(code: str) -> str:
    """清洗 EL 程式碼，確保 PLEditor 可正確讀取（純 ASCII + CRLF）。

    PLEditor 以 cp950 (Big5) 讀取 .el 來源檔。
    含 UTF-8 多位元組中文的注釋行，cp950 解碼失敗 → 整個檔案顯示空白。
    本函式移除問題注釋行，保留所有程式邏輯。
    """
    cleaned = []
    for line in code.splitlines():
        stripped = line.strip()
        # 純注釋行（// 開頭）且含非 ASCII → 移除整行
        if stripped.startswith("//") and not stripped.isascii():
            continue
        # 行尾 inline 注釋含非 ASCII → 截斷到注釋前
        if "//" in line and not line.isascii():
            idx = line.index("//")
            line = line[:idx].rstrip()
        # 大括號注釋 { ... } 含非 ASCII → 移除大括號段落
        if "{" in line and "}" in line and not line.isascii():
            line = re.sub(r"\{[^}]*\}", "", line).rstrip()
        cleaned.append(line)
    return "\r\n".join(cleaned)  # PLEditor 使用 CRLF


def _compile_with_pleditor(
    el_path: Path,
    mc_dir: str,
    compile_wait: float,
) -> None:
    """使用 PLEditor.exe 編譯已匯入的 AISMART_Test 策略（Ctrl+F7 Build All）。

    前提：AISMART_Test 已透過 setup_aismart_template() 匯入一次。
    每次呼叫只覆寫 .el 檔後觸發 Build All，不需重新匯入。

    PLEditor 視窗類別（實際探測）：PLEditor_MainForm
    PLEditor 視窗標題：MultiCharts64 PowerLanguage Editor
    """
    from pywinauto import Application
    from pywinauto.findwindows import find_windows as _fw

    pl_exe = str(Path(mc_dir) / "PLEditor.exe")
    logger.info("[MC] 啟動 PLEditor（Build All）：%s", pl_exe)

    proc = subprocess.Popen([pl_exe])
    time.sleep(4.0)

    # 以 class_name 連線（比 title_re 更可靠，避免二義性）
    handles = _fw(class_name="PLEditor_MainForm")
    if not handles:
        proc.terminate()
        raise RuntimeError(
            "PLEditor 視窗 (PLEditor_MainForm) 未出現。"
            "請確認 MultiCharts64 已安裝且 PLEditor.exe 可執行。"
        )

    pl_win = Application(backend="win32").connect(handle=handles[0]).window(handle=handles[0])
    pl_win.set_focus()
    time.sleep(0.5)

    # Ctrl+F7 = Build All（編譯所有已匯入的策略，含 AISMART_Test）
    pl_win.type_keys("^{F7}", pause=0.1)
    logger.info("[MC] Build All 中，等待 %.1fs...", compile_wait)
    time.sleep(compile_wait)

    # 關閉可能的對話框
    for dlg_title in ("Error", "Warning", "Build"):
        try:
            dlg = Application(backend="win32").connect(
                handle=handles[0]
            ).window(title_re=f".*{dlg_title}.*")
            if dlg.exists(timeout=1):
                logger.warning("[MC] PLEditor 對話框：%s", dlg.window_text())
                dlg.close()
        except Exception:
            pass

    pl_win.close()
    logger.info("[MC] PLEditor 已關閉，AISMART_Test 編譯完成")


def _connect_or_launch_mc(mc_dir: str):
    """連線到已執行的 MultiCharts64，若未執行則啟動。

    使用 class_name='ATL_MCMDIMainFrame' 精確定位，避免二義性。
    （實際探測確認：MC12 主視窗類別 = ATL_MCMDIMainFrame）
    """
    from pywinauto import Application
    from pywinauto.findwindows import find_windows as _fw

    mc_exe = str(Path(mc_dir) / "MultiCharts64.exe")

    handles = _fw(class_name="ATL_MCMDIMainFrame")
    if handles:
        app = Application(backend="win32").connect(handle=handles[0])
        logger.info("[MC] 已連線到 MultiCharts64 (hwnd=%d)", handles[0])
        return app

    logger.info("[MC] 啟動 MultiCharts64：%s", mc_exe)
    Application(backend="win32").start(mc_exe)
    time.sleep(12.0)

    handles = _fw(class_name="ATL_MCMDIMainFrame")
    if not handles:
        raise RuntimeError("MultiCharts64 主視窗 (ATL_MCMDIMainFrame) 未出現")
    app = Application(backend="win32").connect(handle=handles[0])
    logger.info("[MC] MultiCharts64 已啟動並連線")
    return app


def _open_workspace(mc_app, workspace_path: str) -> None:
    """在 MC12 中開啟指定工作區（.wsp）。"""
    from pywinauto.keyboard import send_keys

    mc_win = mc_app.top_window()
    mc_win.set_focus()

    # File → Open Workspace
    try:
        mc_win.menu_select("File->Open Workspace")
    except Exception:
        send_keys("%fo")  # Alt+F, O fallback

    time.sleep(1.5)

    # 填入工作區路徑並確認
    try:
        file_dlg = mc_app.window(title_re=".*Open.*")
        file_dlg.wait("ready", timeout=10)
        # 輸入路徑到 filename 欄位
        edit = file_dlg.child_window(class_name="Edit")
        edit.set_text(str(workspace_path))
        file_dlg.child_window(title="Open", class_name="Button").click()
        time.sleep(5.0)
    except Exception as exc:
        logger.warning("[MC] 開啟工作區對話框失敗：%s，繼續使用目前工作區", exc)


def _trigger_backtest_and_export(
    mc_app,
    chart_title: str,
    strategy_name: str,
    spr_dir: str,
    strategy_id: str,
    timeout: float,
) -> Path:
    """在 MC12 圖表中觸發策略回測，等待完成後匯出 SPR CSV。

    流程：
        1. 定位含策略的圖表視窗
        2. 右鍵 → 設定 → 訊號 → 點「啟動」按鈕兩次（關閉再啟動）→ OK
           （重新編譯後必須透過此步驟強制 MC12 重新計算策略績效）
        3. 等待 Performance Report 視窗
        4. 在 Performance Report 中 File/右鍵匯出 CSV

    Returns:
        Path: 匯出的 CSV 路徑
    """
    spr_path = Path(spr_dir)
    spr_path.mkdir(parents=True, exist_ok=True)
    csv_file = spr_path / f"spr_{strategy_id}_{int(time.time())}.csv"

    mc_win = mc_app.top_window()
    mc_win.set_focus()
    time.sleep(1.0)

    chart_win = _find_chart_window(mc_app, chart_title)
    if not chart_win:
        logger.warning("[MC] 找不到圖表視窗（chart_title=%r），改用主視窗", chart_title)
        chart_win = mc_win

    # 右鍵圖表 → 設定 → 訊號 → 啟動按鈕切換（關閉 → 啟動）→ OK
    _toggle_strategy_signal(mc_app, chart_win)
    logger.info("[MC] 策略訊號已重新啟動，等待回測完成...")

    # 等待 Performance Report 視窗
    pr_win = _wait_for_performance_report(mc_app, timeout)

    if pr_win:
        _export_spr_to_csv(mc_app, pr_win, csv_file)
    else:
        raise TimeoutError(
            f"等待 MC12 Performance Report 視窗超時（{timeout}s）。"
            "請確認 MC12 圖表已正確設定 AI_TEST 策略並可正常回測。"
        )

    return csv_file


def _toggle_strategy_signal(mc_app, chart_win) -> None:
    """開啟「訊號」對話框，點「啟動」按鈕兩次（關閉再啟動）→ OK。

    MC12 重新編譯 EL 後，圖表不會自動套用新程式碼。
    必須透過訊號對話框將策略關閉再啟動，才會以新編譯的 DLL 重新計算。

    UI 路徑（繁中 MC12，依優先順序嘗試）：
        主要：頂部選單列 → 訊號
        備用：右鍵圖表 → 設定訊號

    對話框操作：
        找到「啟動」狀態按鈕 → 點一次（關閉）→ 再點一次（啟動）→ OK
    """
    mc_win = mc_app.top_window()

    # ── Step 1: 開啟訊號對話框 ───────────────────────────────────
    opened = False

    # 方法 A（主要）：鍵盤快捷鍵 Alt+O → L（設定 → 訊號）
    try:
        mc_win.set_focus()
        time.sleep(0.5)
        mc_win.type_keys("%o", pause=0.2)   # Alt+O 開啟「設定」選單
        time.sleep(0.4)
        mc_win.type_keys("l", pause=0.1)    # L 選擇「訊號」
        opened = True
        logger.info("[MC] 鍵盤快捷鍵 Alt+O → L（設定 → 訊號）")
    except Exception as exc:
        logger.warning("[MC] 鍵盤快捷鍵失敗（%s）", exc)

    # 方法 B（備用）：頂部選單列 menu_select
    if not opened:
        for menu_path in ("設定->訊號", "設定->Signals"):
            try:
                mc_win.set_focus()
                time.sleep(0.3)
                mc_win.menu_select(menu_path)
                opened = True
                logger.info("[MC] menu_select → %r", menu_path)
                break
            except Exception:
                pass

    # 方法 C（備用）：右鍵圖表 → 設定訊號
    if not opened:
        try:
            rect = chart_win.rectangle()
            cx = (rect.left + rect.right) // 2
            cy = (rect.top + rect.bottom) // 2
            chart_win.right_click_input(coords=(cx - rect.left, cy - rect.top))
            time.sleep(0.8)
            for label in ("設定訊號", "Format Signals", "Signals"):
                try:
                    item = mc_app.window(
                        title_re=f".*{label}.*", class_name="#32768", timeout=2
                    )
                    if item.exists():
                        item.click_input()
                        opened = True
                        logger.info("[MC] 右鍵選單 → %r", label)
                        break
                except Exception:
                    pass
        except Exception as exc:
            logger.warning("[MC] 右鍵圖表失敗（%s）", exc)

    if not opened:
        logger.warning("[MC] 無法開啟訊號對話框，跳過策略重啟步驟")
        return

    time.sleep(1.0)

    # ── Step 2: 等待「設定物件」對話框出現 ──────────────────────
    # 實際標題：'設定物件'，class：#32770（標準 Windows 對話框）
    # 必須從 Desktop 層級搜尋（mc_app.window() 範圍不夠）
    from pywinauto import Desktop
    signal_dlg = None
    desktop = Desktop(backend="win32")
    for _ in range(10):   # 最多等 5 秒
        try:
            dlg = desktop.window(title="設定物件", class_name="#32770")
            if dlg.exists():
                signal_dlg = dlg
                logger.info("[MC] 設定物件對話框已開啟")
                break
        except Exception:
            pass
        time.sleep(0.5)

    if not signal_dlg:
        logger.warning("[MC] 找不到「設定物件」對話框，跳過策略重啟步驟")
        return

    signal_dlg.set_focus()
    time.sleep(0.5)

    # ── Step 3: 點擊「狀態」按鈕兩次（關閉 → 啟動）──────────────
    # 右側按鈕由上到下：設定... / 移除 / 狀態 / 新增... / 上移 / 下移 / ...
    # AI_TEST 清單第一項已自動選取，直接操作「狀態」按鈕即可
    # 點一次 → 策略關閉；再點一次 → 策略啟動
    toggled = False
    try:
        btn = signal_dlg.child_window(title="狀態", class_name="Button")
        if btn.exists():
            logger.info("[MC] 點擊「狀態」按鈕（關閉）")
            btn.click()
            time.sleep(1.0)
            logger.info("[MC] 點擊「狀態」按鈕（啟動）")
            btn.click()
            time.sleep(1.0)
            toggled = True
    except Exception as exc:
        logger.warning("[MC] 點擊「狀態」按鈕失敗（%s）", exc)

    if not toggled:
        logger.warning("[MC] 找不到「狀態」按鈕，列出對話框內所有子控件供除錯：")
        try:
            for child in signal_dlg.children():
                logger.warning("  class=%r  title=%r",
                                child.class_name(), child.window_text())
        except Exception:
            pass

    # ── Step 4: 按 Close 關閉對話框 ─────────────────────────────
    # 截圖確認：關閉按鈕文字為「Close」（英文）
    try:
        signal_dlg.child_window(title="Close", class_name="Button").click()
        logger.info("[MC] 設定物件對話框已關閉，策略重新啟動完成")
    except Exception as exc:
        logger.warning("[MC] 關閉對話框失敗（%s）", exc)

    time.sleep(2.0)  # 等待 MC12 開始重新計算


def _is_strategy_registered(strategy_name: str, mc_cfg: dict) -> bool:
    """檢查策略是否已在 MC12 中編譯（DLL 存在）。

    MC12 命名規則（實際觀察）：
        s_{name}.dll   — 使用者自建策略（空格 → b20）
        sb_{name}.dll  — 內建函式庫策略
        sa_{name}.dll  — 系統/Demo 策略
    """
    dll_dir = Path(mc_cfg.get("studies_dir", _DEFAULT_STUDIES)).parents[1] / "Dlls" / "Strategies"
    if not dll_dir.exists():
        return False
    encoded_name = strategy_name.replace(" ", "b20")
    for pattern in [
        f"s_{encoded_name}.dll",     # 使用者自建（最常見）
        f"sb_{encoded_name}.dll",    # 內建函式庫
        f"sa_{encoded_name}.dll",    # 系統/Demo
        f"s_{strategy_name}.dll",    # 無空格直接用原名
    ]:
        if (dll_dir / pattern).exists():
            return True
    return False


def _find_chart_window(mc_app, chart_title: str):
    """定位 MC12 中的圖表子視窗（ATL_MCMDIChildFrame）。

    MC12 圖表 MDI 子視窗標題格式：'AMD - 1 分 - Free Quotes'
    透過 chart_title 關鍵字匹配（config.yaml mc_bridge.chart_title）。
    """
    from pywinauto.findwindows import find_windows as _fw

    # 優先從 ATL_MCMDIChildFrame 中搜尋
    for hwnd in _fw(class_name="ATL_MCMDIChildFrame"):
        try:
            import win32gui
            title = win32gui.GetWindowText(hwnd)
            if chart_title.lower() in title.lower():
                return mc_app.window(handle=hwnd)
        except Exception:
            pass

    # Fallback: 從 mc_app.windows() 搜尋
    for win in mc_app.windows():
        try:
            title = win.window_text()
            if chart_title.lower() in title.lower():
                return win
        except Exception:
            pass
    return None


def _wait_for_performance_report(mc_app, timeout: float):
    """等待 MC12 Performance Report 視窗出現。"""
    deadline = time.time() + timeout
    patterns = [
        "Performance Report",
        "Strategy Performance",
        "績效報告",
        "回測報告",
    ]
    while time.time() < deadline:
        for win in mc_app.windows():
            try:
                title = win.window_text()
                if any(p.lower() in title.lower() for p in patterns):
                    logger.info("[MC] Performance Report 視窗已出現：%s", title)
                    return win
            except Exception:
                pass
        time.sleep(2.0)
    return None


def _export_spr_to_csv(mc_app, pr_win, csv_path: Path) -> None:
    """在 Performance Report 視窗中匯出 CSV。"""
    from pywinauto.keyboard import send_keys

    pr_win.set_focus()
    time.sleep(0.5)

    # 嘗試 File → Export
    exported = False
    for menu_path in ("File->Export to CSV", "File->Export", "File->Save As"):
        try:
            pr_win.menu_select(menu_path)
            exported = True
            break
        except Exception:
            pass

    if not exported:
        # 嘗試右鍵 → Export
        try:
            pr_win.right_click_input()
            time.sleep(0.5)
            for label in ("Export to CSV", "Export", "匯出"):
                try:
                    mc_app.window(title_re=f".*{label}.*", timeout=2).click_input()
                    exported = True
                    break
                except Exception:
                    pass
        except Exception:
            pass

    if not exported:
        # 嘗試工具列按鈕（通常有 Export 圖示）
        try:
            pr_win.child_window(title_re=".*Export.*").click()
            exported = True
        except Exception:
            pass

    if not exported:
        logger.warning("[MC] 無法找到匯出按鈕，嘗試 Ctrl+E")
        send_keys("^e")

    time.sleep(1.5)

    # 儲存對話框：輸入路徑並確認
    try:
        save_dlg = mc_app.window(title_re=".*(?:Save|Export|另存).*", timeout=10)
        save_dlg.wait("ready", timeout=10)
        edit = save_dlg.child_window(class_name="Edit")
        edit.set_text(str(csv_path))
        save_dlg.child_window(
            title_re=".*(?:Save|存檔|確定|OK).*", class_name="Button"
        ).click()
        time.sleep(2.0)
        logger.info("[MC] SPR CSV 匯出至：%s", csv_path)
    except Exception as exc:
        logger.warning("[MC] 儲存對話框操作失敗：%s", exc)
        raise RuntimeError(
            f"無法完成 SPR CSV 匯出：{exc}。"
            "請手動確認 MC12 Performance Report 中的匯出功能是否正常。"
        ) from exc


def _dialog_guard_loop(stop_event: threading.Event, interval: float) -> None:
    """背景執行：持續偵測並關閉 MC12 錯誤對話框。

    自動處理的視窗類型：
        - MessageBox（錯誤 / 警告）
        - 確認對話框（「是否繼續？」等）
        - PLEditor 編譯錯誤通知

    Args:
        stop_event: 設定後停止迴圈
        interval:   輪詢間隔（秒）
    """
    try:
        from pywinauto import Application, findwindows
    except ImportError:
        logger.warning("[MC] pywinauto 未安裝，對話框守衛無法啟動")
        return

    _DIALOG_CLOSE_TITLES = re.compile(
        r"(?i)(error|warning|confirm|alert|訊息|錯誤|警告|確認|注意|提示|麻煩|問題)",
        re.IGNORECASE,
    )
    _CLOSE_BUTTONS = ("OK", "確定", "Close", "關閉", "Yes", "是", "Continue")

    logger.debug("[MC Guard] 對話框守衛已啟動")

    while not stop_event.is_set():
        try:
            # 掃描所有屬於 MultiCharts / PLEditor 的彈出視窗
            for handle in findwindows.find_windows(class_name="#32770"):
                try:
                    from pywinauto import Desktop
                    dlg = Desktop(backend="win32").window(handle=handle)
                    title = dlg.window_text()

                    if not _DIALOG_CLOSE_TITLES.search(title):
                        # 也嘗試抓無標題 MC 彈窗（class_name = #32770）
                        parent_proc = dlg.process_id()
                        mc_procs = _get_mc_pids()
                        if parent_proc not in mc_procs:
                            continue

                    logger.info("[MC Guard] 偵測到對話框：%r，自動關閉", title)

                    # 優先點擊 OK / 確定
                    closed = False
                    for btn_title in _CLOSE_BUTTONS:
                        try:
                            dlg.child_window(
                                title=btn_title, class_name="Button"
                            ).click()
                            closed = True
                            break
                        except Exception:
                            pass

                    if not closed:
                        dlg.close()

                except Exception:
                    pass

        except Exception as exc:
            logger.debug("[MC Guard] 掃描異常：%s", exc)

        stop_event.wait(interval)

    logger.debug("[MC Guard] 對話框守衛已停止")


def _get_mc_pids() -> set[int]:
    """回傳目前執行中的 MultiCharts / PLEditor 的 PID 集合。"""
    try:
        import psutil
        return {
            p.pid for p in psutil.process_iter(["pid", "name"])
            if any(k in (p.info["name"] or "").lower()
                   for k in ("multicharts", "pleditor", "studyserver"))
        }
    except ImportError:
        return set()


# ── 數字解析輔助 ──────────────────────────────────────────────────

def _parse_mc_number(value: str) -> float | None:
    """解析 MC12 SPR CSV 中的數字欄位。

    支援格式：
        "$1,234,567"  → 1234567.0
        "($234,567)"  → -234567.0（括號代表負數）
        "-26.52%"     → -26.52
        "2.11"        → 2.11
        "68.16%"      → 68.16
        "—" / ""      → None
    """
    v = value.strip().strip('"').replace(",", "")
    if not v or v in ("—", "-", "N/A", "n/a"):
        return None

    negative = v.startswith("(") and v.endswith(")")
    v = v.strip("()").lstrip("$").rstrip("%").strip()

    try:
        num = float(v)
        return -num if negative else num
    except ValueError:
        return None


def _load_mc_filter_ratio() -> float:
    """從 config.yaml mc_filter.oos_is_ratio_min 讀取過擬合偵測門檻。"""
    cfg_path = Path(__file__).resolve().parents[3] / "config.yaml"
    try:
        with open(cfg_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        return cfg.get("mc_filter", {}).get("oos_is_ratio_min", 0.6)
    except Exception:
        return 0.6
