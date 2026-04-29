"""跨 session 策略生成記憶 — fail_patterns + confirmed examples + daily budget。

三大功能：
    1. **反面教材**：load_fail_patterns() 從 memory/fail_patterns.md 讀取最近
        N 條失敗記錄，塞進 LLM system prompt，避免重複生成同類垃圾
    2. **正面教材**：sample_confirmed_examples() 從 confirmed/ 抽 N 個已通過
        MTC 驗證的策略，當 few-shot 範例
    3. **保險絲**：check_daily_budget() 防止單日無限重試燒掉 LLM token；
        consecutive_type_failures() 偵測某類型連續失敗，建議換 type

檔案位置（皆於專案根目錄）：
    memory/fail_patterns.md   — 反面教材累積
    memory/daily_budget.json  — 今日預算狀態
    confirmed/*.py            — MTC 人工驗證通過的策略

Usage:
    from src.core.ai_engine.memory import StrategyMemory

    mem = StrategyMemory()

    # 預檢
    ok, reason = mem.check_daily_budget()
    if not ok:
        sys.exit(reason)

    # 載入記憶
    fail_patterns = mem.load_fail_patterns(limit=20)
    examples      = mem.sample_confirmed_examples("trend", n=2)

    # 記錄結果
    mem.record_attempt(success=True, tokens_used=5000)
    mem.append_fail_pattern(name, type_, direction, code_summary, reasons)
"""
from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]


@dataclass
class StrategyMemory:
    """跨 session 策略生成記憶管理器。"""

    fail_log:      Path = _PROJECT_ROOT / "memory" / "fail_patterns.md"
    daily_budget:  Path = _PROJECT_ROOT / "memory" / "daily_budget.json"
    confirmed_dir: Path = _PROJECT_ROOT / "confirmed"

    def __post_init__(self) -> None:
        self.fail_log.parent.mkdir(parents=True, exist_ok=True)
        self.confirmed_dir.mkdir(parents=True, exist_ok=True)
        if not self.fail_log.exists():
            self.fail_log.write_text(
                "# AISMART 策略生成失敗記錄\n\n"
                "此檔案由系統自動維護，記錄歷次未通過 python_filter 的策略。\n"
                "下次生成時會塞進 LLM system prompt 當反面教材。\n\n",
                encoding="utf-8",
            )

    # ── 反面教材：fail_patterns.md ───────────────────────────────────

    def load_fail_patterns(self, limit: int = 20) -> str:
        """讀取最近 N 條失敗記錄，回傳 markdown 文字。

        用 `## ` 標題切段，取最後 N 段。回傳格式可直接 append 到 system prompt。
        """
        if not self.fail_log.exists():
            return ""

        text = self.fail_log.read_text(encoding="utf-8")
        # 以 "\n## " 切段（保留各段標題）
        parts = text.split("\n## ")
        if len(parts) <= 1:
            return ""

        # parts[0] 是檔頭說明，parts[1:] 是各條失敗記錄
        records = ["## " + p.strip() for p in parts[1:] if p.strip()]
        recent = records[-limit:]
        if not recent:
            return ""

        return (
            "# 過往失敗教訓（請避免重複下列設計）\n\n"
            + "\n\n".join(recent)
        )

    def append_fail_pattern(
        self,
        strategy_name:  str,
        strategy_type:  str,
        direction:      str,
        code_summary:   str,
        fail_reasons:   list[str],
        timeframe:      str = "1min",
    ) -> None:
        """append 一條失敗記錄到 fail_patterns.md。"""
        date = datetime.now().strftime("%Y-%m-%d %H:%M")
        record = (
            f"\n## {date} {strategy_type} / {direction} / {timeframe}\n"
            f"- 策略：{strategy_name}\n"
            f"- 設計摘要：{code_summary}\n"
            f"- 失敗：{'、'.join(fail_reasons)}\n"
        )
        with self.fail_log.open("a", encoding="utf-8") as f:
            f.write(record)
        logger.info("已 append 失敗記錄：%s", strategy_name)

    # ── 正面教材：confirmed/ ────────────────────────────────────────

    def sample_confirmed_examples(
        self,
        strategy_type: str | None = None,
        n: int = 2,
    ) -> list[dict]:
        """從 confirmed/ 抽 N 個策略當 few-shot 範例。

        Args:
            strategy_type: 若指定，優先抽同類型（從 .py 內 CATEGORY 比對）；
                           不足 n 個時補其他類型
            n:             要抽的數量

        Returns:
            list[{"name": str, "category": str, "code": str}]
        """
        if not self.confirmed_dir.exists():
            return []

        py_files = sorted(self.confirmed_dir.glob("*.py"))
        if not py_files:
            return []

        # 解析每個檔的 CATEGORY 與 NAME
        all_records: list[dict] = []
        for fp in py_files:
            try:
                code = fp.read_text(encoding="utf-8")
                category = self._extract_token(code, "CATEGORY") or "unknown"
                name     = self._extract_token(code, "NAME") or fp.stem
                all_records.append({
                    "name": name,
                    "category": category,
                    "code": code,
                    "filepath": fp,
                })
            except Exception as exc:
                logger.warning("無法讀取 confirmed/%s: %s", fp.name, exc)

        if not all_records:
            return []

        # 優先同類型，不夠再補其他
        same = [r for r in all_records if r["category"] == strategy_type]
        other = [r for r in all_records if r["category"] != strategy_type]
        random.shuffle(same)
        random.shuffle(other)
        picked = (same + other)[:n]

        # 不回傳 filepath（避免序列化問題）
        return [{k: v for k, v in r.items() if k != "filepath"} for r in picked]

    @staticmethod
    def _extract_token(code: str, key: str) -> str:
        """從 Python 程式碼中萃取 NAME / CATEGORY 字串值。"""
        import re
        m = re.search(rf'{key}\s*=\s*["\']([^"\']+)["\']', code)
        return m.group(1) if m else ""

    # ── 保險絲：daily_budget.json ───────────────────────────────────

    def _load_budget(self) -> dict:
        """讀取今日預算 JSON；若日期不符或不存在則重置。"""
        today = datetime.now().strftime("%Y-%m-%d")
        default = {
            "date": today,
            "attempts": 0,
            "successes": 0,
            "tokens_used": 0,
            "type_consecutive_fails": {},
        }
        if not self.daily_budget.exists():
            return default
        try:
            data = json.loads(self.daily_budget.read_text(encoding="utf-8"))
            if data.get("date") != today:
                # 跨日重置（保留 type_consecutive_fails，因為失敗類型應跨日累積）
                return {**default,
                        "type_consecutive_fails": data.get("type_consecutive_fails", {})}
            return {**default, **data}
        except Exception as exc:
            logger.warning("讀取 daily_budget.json 失敗：%s — 重置", exc)
            return default

    def _save_budget(self, data: dict) -> None:
        self.daily_budget.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def check_daily_budget(
        self,
        max_failures: int = 10,
        max_tokens:   int = 1_000_000,
    ) -> tuple[bool, str]:
        """檢查今日是否已用盡預算。

        Returns:
            (是否可繼續, 原因說明)
        """
        b = self._load_budget()
        failures = b["attempts"] - b["successes"]
        if failures >= max_failures:
            return False, (
                f"今日已失敗 {failures} 次達上限（{max_failures}）。"
                f"請明日再試或用 --force 強制執行。"
                f"建議檢查 memory/fail_patterns.md 找出失敗模式。"
            )
        if b["tokens_used"] >= max_tokens:
            return False, (
                f"今日已用 {b['tokens_used']:,} tokens 達上限（{max_tokens:,}）。"
                f"請明日再試或用 --force 強制執行。"
            )
        return True, (
            f"今日已用 {failures}/{max_failures} 次失敗、"
            f"{b['tokens_used']:,}/{max_tokens:,} tokens"
        )

    def record_attempt(
        self,
        success:      bool,
        tokens_used:  int,
        strategy_type: str | None = None,
    ) -> None:
        """記錄一次嘗試（成功或失敗）。"""
        b = self._load_budget()
        b["attempts"] += 1
        if success:
            b["successes"] += 1
        b["tokens_used"] += tokens_used

        # 更新類型連續失敗計數（成功則歸零）
        if strategy_type:
            counts = b.setdefault("type_consecutive_fails", {})
            if success:
                counts[strategy_type] = 0
            else:
                counts[strategy_type] = counts.get(strategy_type, 0) + 1

        self._save_budget(b)

    def consecutive_type_failures(self, strategy_type: str) -> int:
        """回傳此 strategy_type 連續失敗次數。"""
        b = self._load_budget()
        return b.get("type_consecutive_fails", {}).get(strategy_type, 0)

    def budget_summary(self) -> str:
        """回傳今日預算簡報字串。"""
        b = self._load_budget()
        return (
            f"今日：{b['attempts']} 次嘗試 / {b['successes']} 次通過 / "
            f"{b['tokens_used']:,} tokens"
        )
