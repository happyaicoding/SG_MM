"""scripts/generate_and_compile.py — AI 生成 → Python 初篩 → 編譯 → 待MTC確認

完整流程（A3 版本：含跨 session 學習與保險絲）：
    [STEP 0/5] 預檢：今日預算、連續失敗類型警示、載入 fail_patterns + confirmed
    [STEP 1/5] AI 生成 + Python 初篩重生迴圈（最多 3 次）
                每次生成後立刻跑 vectorbt 回測 → 比對 python_filter 門檻
                未通過：把失敗原因餵回 LLM，重新生成
                全部失敗：移到 rejected/<name>/ 並記錄到 fail_patterns.md
    [STEP 2/5] 補生成 EL（只有通過初篩才執行，省 token）
    [STEP 3/5] PLEditor 編譯
    [STEP 4/5] 複製 .py / .el / .metrics.txt 到 待MTC確認/
    [STEP 5/5] 顯示今日預算狀態

執行方式：
    python scripts/generate_and_compile.py
    python scripts/generate_and_compile.py --type trend --direction both
    python scripts/generate_and_compile.py --type mean_reversion --max-attempts 5

CLI 參數：
    --type           策略類型：trend / mean_reversion / opening / scalp / swing / pattern
    --direction      方向：both / long / short
    --max-attempts   最多重生次數（預設 3）
    --timeframe      回測 K 棒週期（預設 1min；如 5m / 15m / 30m）
    --skip-filter    跳過 Python 初篩，沿用舊行為（debug 用）
    --no-memory      不讀寫 fail_patterns / confirmed（純測試用）
    --force          忽略保險絲（單日上限、連續失敗警示），強制執行
"""
from __future__ import annotations

import sys
import shutil
import logging
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%H:%M:%S",
)

from src.core.backtest.mc_bridge import (
    _ensure_admin,
    _load_mc_config,
    _write_el_file,
    _compile_with_pleditor,
    _is_strategy_registered,
    _DEFAULT_MC_DIR,
    _DEFAULT_STUDIES,
)
from src.core.ai_engine.generator import StrategyGenerator
from src.core.ai_engine.memory import StrategyMemory
from src.core.ai_engine.retriever import StrategyRetriever
from src.core.ai_engine.strategy_loader import load_strategy_from_file
from src.core.backtest.runner import BacktestRunner

# 自動 UAC 提升（PLEditor 編譯需要 Admin）
_ensure_admin()

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PENDING_DIR  = PROJECT_ROOT / "待MTC確認"
REJECTED_DIR = PROJECT_ROOT / "rejected"


# ─────────────────────────────────────────────────────────────────
# CLI 參數
# ─────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AI 生成 + Python 初篩 + PLEditor 編譯")
    parser.add_argument("--type",         default="trend",
                        choices=["trend", "mean_reversion", "opening",
                                 "scalp", "swing", "pattern"],
                        help="小分類（策略邏輯類型，預設：trend）")
    parser.add_argument("--holding",      default="daytrade",
                        choices=["daytrade", "swing"],
                        help="大分類（持倉類型：daytrade=當沖 / swing=波段，預設：daytrade）")
    parser.add_argument("--direction",    default="both",
                        choices=["both", "long", "short"],
                        help="交易方向（預設：both）")
    parser.add_argument("--max-attempts", type=int, default=3,
                        help="Python 初篩失敗時最多重生次數（預設 3）")
    parser.add_argument("--timeframe",    default=None,
                        help="強制覆寫回測 K 棒週期（預設讀策略 TIMEFRAME 屬性）")
    parser.add_argument("--skip-filter",  action="store_true",
                        help="跳過 Python 初篩（debug 用）")
    parser.add_argument("--no-memory",    action="store_true",
                        help="不讀寫 fail_patterns / confirmed（純測試用）")
    parser.add_argument("--no-library",   action="store_true",
                        help="不從 library/ 檢索實戰範例（純測試用）")
    parser.add_argument("--library-k",    type=int, default=2,
                        help="從 library 檢索的範例數量（預設 2）")
    parser.add_argument("--force",        action="store_true",
                        help="忽略保險絲，強制執行")
    return parser.parse_args()


# ─────────────────────────────────────────────────────────────────
# 步驟函式
# ─────────────────────────────────────────────────────────────────

def step0_precheck(args, memory):
    """STEP 0/5：預檢預算、警示連續失敗、載入記憶與實戰範例。

    Returns:
        (fail_patterns_md, confirmed_examples, library_prompt)
    """
    print("\n[STEP 0/5] 預檢...")

    fail_patterns: str = ""
    confirmed_examples: list = []
    library_prompt: str = ""

    if memory is not None:
        # 預算檢查
        ok, reason = memory.check_daily_budget()
        print(f"  {reason}")
        if not ok and not args.force:
            print(f"\n[STOP] {reason}")
            sys.exit(1)
        elif not ok:
            print("  --force：忽略保險絲，繼續執行")

        # 連續類型失敗警示
        consec = memory.consecutive_type_failures(args.type)
        if consec >= 3:
            print(
                f"  警告：{args.type} 已連續 {consec} 次 run 全部失敗\n"
                f"        建議改用其他類型（mean_reversion / opening / scalp / swing / pattern）"
            )
            if not args.force:
                print(f"        繼續執行 {args.type} 請加 --force")
                sys.exit(1)

        # 載入 memory 記憶
        fail_patterns = memory.load_fail_patterns(limit=20)
        confirmed_examples = memory.sample_confirmed_examples(args.type, n=2)
        fail_count = fail_patterns.count("\n## ") if fail_patterns else 0
        print(f"  記憶：{fail_count} 條失敗教訓、{len(confirmed_examples)} 個 confirmed 範例")
    else:
        print("  --no-memory：跳過記憶載入與預算檢查")

    # 載入 library 實戰範例（向量檢索）
    if args.no_library:
        print("  --no-library：跳過實戰範例檢索")
    else:
        try:
            retriever = StrategyRetriever()
            library_prompt = retriever.find_similar_as_prompt(
                category=args.type,
                holding_type=args.holding,
                direction=args.direction,
                extra_query="risk management, intraday session filter, stop loss",
                k=args.library_k,
            )
            n = library_prompt.count("\n### ") if library_prompt else 0
            if n:
                print(f"  library：檢索 {n} 支實戰範例（{args.holding}/{args.type} top-{args.library_k}）")
            else:
                print(f"  library：{args.holding}/{args.type} 內無實戰範例（請先 python scripts/index_library.py）")
        except ModuleNotFoundError as exc:
            print(f"  [WARN] retriever 依賴缺失：{exc}（略過實戰範例）")
        except Exception as exc:
            print(f"  [WARN] retriever 失敗：{exc}（略過實戰範例）")

    return fail_patterns, confirmed_examples, library_prompt


def step1_generate_with_filter(args, gen, memory, fail_patterns, examples,
                                library_prompt: str = ""):
    """STEP 1/5：AI 生成 + Python 初篩重生迴圈。

    Returns:
        (strategy, bt_result)
        通過時 bt_result.passed_filter == True
        全失敗時 strategy 可能是最後一版（用於 rejected/ 整理），bt_result 可能為 None
    """
    print(f"\n[STEP 1/5] AI 生成 + Python 初篩（最多 {args.max_attempts} 次）...")

    runner = BacktestRunner() if not args.skip_filter else None
    session_feedback = ""
    last_strategy = None
    last_result = None

    for attempt in range(1, args.max_attempts + 1):
        print(f"\n  [{attempt}/{args.max_attempts}] AI 生成中...")
        try:
            strategy = gen.generate(
                strategy_type=args.type,
                direction=args.direction,
                holding_period="intraday" if args.holding == "daytrade" else "swing",
                holding_type=args.holding,
                save=True,
                with_el=False,                  # 先不轉 EL，省 token
                fail_patterns=fail_patterns,
                confirmed_examples=examples,
                library_prompt=library_prompt,
                research_summary=session_feedback,
            )
        except Exception as exc:
            print(f"  [ERROR] 生成失敗：{exc}")
            if memory:
                memory.record_attempt(success=False, tokens_used=0,
                                      strategy_type=args.type)
            continue

        # 決定回測週期：CLI --timeframe 強制覆寫優先，否則讀策略宣告
        tf = args.timeframe or strategy.timeframe
        print(f"  [OK] 策略已生成：{strategy.name} [{strategy.timeframe}]  "
              f"tokens={strategy.total_tokens:,}")
        last_strategy = strategy

        if args.skip_filter:
            print("  --skip-filter：跳過初篩，直接通過")
            return strategy, None

        # 動態載入 + 跑 vectorbt
        try:
            tf_label = f"{tf}（強制覆寫）" if args.timeframe else tf
            print(f"  Python 初篩中（{tf_label}）...")
            loaded = load_strategy_from_file(strategy.filepath)
            bt_result = runner.run(loaded, symbol="TX", timeframe=tf)
            last_result = bt_result
        except Exception as exc:
            print(f"  [ERROR] 初篩執行失敗：{exc}")
            if memory:
                memory.record_attempt(success=False, tokens_used=strategy.total_tokens,
                                      strategy_type=args.type)
            session_feedback = f"上一版執行錯誤（請避免類似實作）：{exc}"
            continue

        # 一行績效摘要
        print(
            f"  → Sharpe={bt_result.sharpe_ratio:.2f} "
            f"MaxDD={bt_result.max_drawdown:.1%} "
            f"PF={bt_result.profit_factor:.2f} "
            f"Trades={bt_result.total_trades} "
            f"=> {'PASS' if bt_result.passed_filter else 'FAIL'}"
        )

        if bt_result.passed_filter:
            if memory:
                memory.record_attempt(success=True, tokens_used=strategy.total_tokens,
                                      strategy_type=args.type)
            return strategy, bt_result

        # 失敗 → 餵回失敗原因
        if memory:
            memory.record_attempt(success=False, tokens_used=strategy.total_tokens,
                                  strategy_type=args.type)
        session_feedback = (
            f"上一版策略 ({strategy.name}) 未達 python_filter 門檻：\n"
            f"  {', '.join(bt_result.filter_reasons)}\n"
            f"請調整邏輯避免上述問題（例如放寬條件提高 Trades 數，"
            f"或簡化過濾以提升 Sharpe）。"
        )

    # 全部失敗
    return last_strategy, last_result


def handle_rejected(strategy, bt_result, args, memory):
    """STEP 1 全失敗 → 移到 rejected/ + 記錄到 fail_patterns.md。"""
    if not strategy:
        print("\n[STOP] 完全沒有產出策略檔（生成階段失敗）")
        return

    name = strategy.name
    target = REJECTED_DIR / name
    target.mkdir(parents=True, exist_ok=True)

    # 移動 .py
    if strategy.filepath and strategy.filepath.exists():
        try:
            shutil.move(str(strategy.filepath), target / strategy.filepath.name)
        except Exception as exc:
            print(f"  [WARN] 無法移動 {strategy.filepath.name}: {exc}")

    # 寫 metrics.txt
    if bt_result:
        (target / "metrics.txt").write_text(bt_result.summary(), encoding="utf-8")

    # 寫 fail_reason.txt
    reasons = bt_result.filter_reasons if bt_result else ["生成階段未產生有效結果"]
    (target / "fail_reason.txt").write_text(
        f"策略：{name}\n"
        f"類型：{args.type} / {args.direction}\n"
        f"重生次數：{args.max_attempts}（全部失敗）\n"
        f"最後失敗原因：{', '.join(reasons)}\n",
        encoding="utf-8",
    )

    # append 到 fail_patterns.md
    if memory and not args.no_memory:
        code_summary = _summarize_code(strategy.code)
        memory.append_fail_pattern(
            strategy_name = name,
            strategy_type = args.type,
            direction     = args.direction,
            code_summary  = code_summary,
            fail_reasons  = reasons,
            timeframe     = (args.timeframe or strategy.timeframe),
        )

    print(f"\n[REJECTED] 已移至 rejected/{name}/")
    print(f"           失敗原因：{', '.join(reasons)}")


def _summarize_code(code: str, max_lines: int = 8) -> str:
    """從 Python 程式碼萃取 generate_signals 前幾行當摘要。"""
    import re
    m = re.search(r"def generate_signals.*?(?=\n    def |\nclass |\Z)",
                  code, re.DOTALL)
    if not m:
        return "(無法擷取摘要)"
    lines = [l.rstrip() for l in m.group(0).splitlines() if l.strip()]
    snippet = " | ".join(lines[:max_lines])
    return snippet[:300] + ("..." if len(snippet) > 300 else "")


def step2_generate_el(gen, strategy):
    """STEP 2/5：補生成 EasyLanguage 版本。"""
    print(f"\n[STEP 2/5] 補生成 EL 程式碼...")
    strategy = gen.generate_el(strategy)
    if not strategy.el_code:
        raise RuntimeError("EL 程式碼為空")
    print(f"  [OK] EL 已生成：{strategy.el_filepath}")
    return strategy


def step3_compile_pleditor(strategy):
    """STEP 3/5：寫入 StudyServer + PLEditor 編譯。"""
    print(f"\n[STEP 3/5] PLEditor 編譯...")
    mc_cfg       = _load_mc_config()
    mc_dir       = mc_cfg.get("mc_dir", _DEFAULT_MC_DIR)
    studies_dir  = mc_cfg.get("studies_dir", _DEFAULT_STUDIES)
    strat_name   = mc_cfg.get("strategy_name", "AI_TEST")
    compile_wait = float(mc_cfg.get("compile_wait", 15.0))

    if not _is_strategy_registered(strat_name, mc_cfg):
        raise RuntimeError(
            f"{strat_name} DLL 不存在，請先在 MC12 手動 Insert AI_TEST 後再執行"
        )

    el_path = _write_el_file(strategy.el_code, strat_name, studies_dir)
    print(f"  [OK] EL 已寫入 StudyServer：{el_path.name}")
    _compile_with_pleditor(el_path, mc_dir, compile_wait)
    print("  [OK] PLEditor Build All 完成")
    return el_path


def step4_save_to_pending(strategy, bt_result):
    """STEP 4/5：複製 .py / .el / .metrics.txt 到 待MTC確認/。"""
    print(f"\n[STEP 4/5] 儲存到 待MTC確認/...")
    PENDING_DIR.mkdir(exist_ok=True)
    saved = []

    if strategy.filepath and strategy.filepath.exists():
        dst = PENDING_DIR / strategy.filepath.name
        shutil.copy2(strategy.filepath, dst)
        saved.append(dst)
        print(f"  [OK] Python：{dst.name}")

    if strategy.el_filepath and strategy.el_filepath.exists():
        dst = PENDING_DIR / strategy.el_filepath.name
        shutil.copy2(strategy.el_filepath, dst)
        saved.append(dst)
        print(f"  [OK] EL    ：{dst.name}")

    if bt_result and strategy.filepath:
        metrics_name = strategy.filepath.stem + ".metrics.txt"
        dst = PENDING_DIR / metrics_name
        dst.write_text(bt_result.summary(), encoding="utf-8")
        saved.append(dst)
        print(f"  [OK] 績效：{dst.name}")

    return saved


def step5_show_budget(memory):
    """STEP 5/5：顯示今日預算狀態。"""
    print(f"\n[STEP 5/5] 預算狀態")
    if memory:
        print(f"  {memory.budget_summary()}")
    else:
        print("  --no-memory：略過")


# ─────────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────────

def main() -> int:
    args = parse_args()

    print("=" * 60)
    print("AISMART — AI 生成 + Python 初篩 + PLEditor 編譯")
    tf_display = args.timeframe if args.timeframe else "(由策略自決)"
    print(f"  持倉：{args.holding}  類型：{args.type}  方向：{args.direction}  週期：{tf_display}")
    print(f"  最大重生：{args.max_attempts} 次  "
          f"skip_filter={args.skip_filter}  "
          f"no_memory={args.no_memory}  force={args.force}")
    print(f"  輸出：{PENDING_DIR}")
    print("=" * 60)

    memory = None if args.no_memory else StrategyMemory()
    gen = StrategyGenerator()

    # STEP 0
    fail_patterns, examples, library_prompt = step0_precheck(args, memory)

    # STEP 1
    strategy, bt_result = step1_generate_with_filter(
        args, gen, memory, fail_patterns, examples, library_prompt,
    )

    # 判斷是否進入失敗處理
    failed = (
        not strategy
        or (not args.skip_filter and not (bt_result and bt_result.passed_filter))
    )

    if failed:
        handle_rejected(strategy, bt_result, args, memory)
        step5_show_budget(memory)
        print(f"\n{'=' * 60}")
        print("[完成 - 未產出可用策略]")
        print('=' * 60)
        return 0

    # STEP 2: 補生成 EL
    try:
        strategy = step2_generate_el(gen, strategy)
    except Exception as exc:
        print(f"\n[ERROR] EL 生成失敗：{exc}")
        return 1

    # STEP 3: PLEditor 編譯
    try:
        step3_compile_pleditor(strategy)
    except Exception as exc:
        print(f"\n[ERROR] 編譯失敗：{exc}")
        return 1

    # STEP 4: 儲存
    saved = step4_save_to_pending(strategy, bt_result)

    # STEP 5: 預算狀態
    step5_show_budget(memory)

    print(f"\n{'=' * 60}")
    print(f"[完成] 策略 {strategy.name} 已準備好，等待 MTC 人工確認")
    print(f"  資料夾：{PENDING_DIR}")
    for f in saved:
        print(f"  - {f.name}")
    print('=' * 60)
    print(f"\n下一步：開啟 MultiCharts 12，將 AI_TEST 策略套用到圖表，")
    print(f"        確認訊號後將檔案移至 confirmed/ 資料夾。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
