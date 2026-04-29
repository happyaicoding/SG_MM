"""AI 優化建議 — 分析回測績效，提出 3 種以上改善方案。

功能：
    AIOptimizer.suggest(strategy_code, backtest_result)
        接收策略原始碼 + 回測績效 dict，
        呼叫 LLM（載入 prompt_templates/optimize.md 作為 system prompt），
        回傳 OptimizationResult（含結構化 suggestions 與原始 JSON）。

支援供應商：
    - ClaudeClient（Anthropic）
    - MiniMaxClient（MiniMax M2.7，Anthropic 相容 API）
    - 任何符合 BaseLLMClient Protocol 的自訂客戶端

Usage:
    from src.core.ai_engine.optimizer_ai import AIOptimizer

    opt = AIOptimizer()   # 從 config.yaml 讀取 LLM 設定

    result = opt.suggest(
        strategy_code=open("src/strategies/ma_cross.py").read(),
        backtest_result={
            "sharpe_ratio": 0.8,
            "max_drawdown": 0.22,
            "profit_factor": 1.1,
            "total_trades": 120,
            "win_rate": 0.48,
            "oos_sharpe": 0.5,
            "overfitting_flag": True,
            "params": {"fast": 10, "slow": 30},
        },
    )

    print(result.analysis)
    for s in result.suggestions:
        print(s["title"], "→", s["description"])
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from src.core.ai_engine.client import BaseLLMClient, create_llm_client

logger = logging.getLogger(__name__)

_TEMPLATE_PATH = Path(__file__).parent / "prompt_templates" / "optimize.md"


# ── 結果資料類別 ──────────────────────────────────────────────────────

@dataclass
class OptimizationResult:
    """AIOptimizer.suggest() 的回傳結果。

    Attributes:
        analysis:     虧損原因分析文字
        suggestions:  改善方案清單，每項含：
                          title               — 方案名稱
                          description         — 說明
                          new_params          — 建議參數 dict（可能為空）
                          expected_improvement — 預期效果描述
        raw_json:     LLM 原始輸出文字（debug 用）
        total_tokens: 本次呼叫累計 token 數
        parse_ok:     True 表示成功解析 JSON；False 表示 fallback 純文字
    """
    analysis: str
    suggestions: list[dict]
    raw_json: str
    total_tokens: int
    parse_ok: bool = True

    def summary(self) -> str:
        """回傳人類可讀的優化摘要。"""
        lines = [f"[分析] {self.analysis}", ""]
        for i, s in enumerate(self.suggestions, 1):
            lines.append(f"方案 {i}：{s.get('title', '未命名')}")
            lines.append(f"  說明：{s.get('description', '')}")
            if s.get("new_params"):
                lines.append(f"  新參數：{s['new_params']}")
            if s.get("expected_improvement"):
                lines.append(f"  預期效果：{s['expected_improvement']}")
        return "\n".join(lines)


# ── AIOptimizer ────────────────────────────────────────────────────────

class AIOptimizer:
    """AI 優化建議 — 分析回測虧損，提出 N 種改善方案。

    Args:
        client: LLM 客戶端（None 時從 config.yaml 建立）
    """

    def __init__(self, client: BaseLLMClient | None = None) -> None:
        self._client = client or create_llm_client()
        self._system_prompt = self._load_system_prompt()

    # ── 主要方法 ──────────────────────────────────────────────────────

    def suggest(
        self,
        strategy_code: str,
        backtest_result: dict,
        *,
        max_suggestions: int = 3,
    ) -> OptimizationResult:
        """分析回測結果，回傳改善方案。

        Args:
            strategy_code:    策略原始碼（Python 字串）
            backtest_result:  回測績效 dict，支援欄位：
                                  sharpe_ratio, max_drawdown, profit_factor,
                                  total_trades, win_rate, oos_sharpe,
                                  overfitting_flag, params (dict)
            max_suggestions:  期望方案數量（僅作為 prompt hint，LLM 實際輸出可能不同）

        Returns:
            OptimizationResult
        """
        user_msg = self._build_user_message(
            strategy_code, backtest_result, max_suggestions
        )

        logger.info(
            "AIOptimizer.suggest() — 策略程式碼 %d 字元，指標：Sharpe=%.2f MaxDD=%.1f%%",
            len(strategy_code),
            backtest_result.get("sharpe_ratio") or 0.0,
            (backtest_result.get("max_drawdown") or 0.0) * 100,
        )

        raw = self._client.chat(
            messages=[{"role": "user", "content": user_msg}],
            system=self._system_prompt,
            max_tokens=2048,
            temperature=0.4,
        )

        result = self._parse_result(raw, self._client.total_tokens)
        logger.info(
            "AIOptimizer 完成：%d 個方案，parse_ok=%s，tokens=%d",
            len(result.suggestions),
            result.parse_ok,
            result.total_tokens,
        )
        return result

    # ── 內部：Prompt 組裝 ─────────────────────────────────────────────

    def _build_user_message(
        self,
        strategy_code: str,
        result: dict,
        max_suggestions: int,
    ) -> str:
        # 格式化績效數字
        sharpe    = result.get("sharpe_ratio")
        max_dd    = result.get("max_drawdown")
        pf        = result.get("profit_factor")
        trades    = result.get("total_trades")
        win_rate  = result.get("win_rate")
        oos_sharpe = result.get("oos_sharpe")
        overfitting = result.get("overfitting_flag", False)
        params    = result.get("params") or {}

        perf_lines = []
        if sharpe is not None:
            perf_lines.append(f"- Sharpe Ratio: {sharpe:.4f}")
        if max_dd is not None:
            perf_lines.append(f"- Max Drawdown: {max_dd:.1%}")
        if pf is not None:
            perf_lines.append(f"- Profit Factor: {pf:.4f}")
        if trades is not None:
            perf_lines.append(f"- Total Trades: {trades}")
        if win_rate is not None:
            perf_lines.append(f"- Win Rate: {win_rate:.1%}")
        if oos_sharpe is not None:
            perf_lines.append(f"- OOS Sharpe: {oos_sharpe:.4f}")
        perf_lines.append(f"- Overfitting Flag: {overfitting}")

        params_str = json.dumps(params, ensure_ascii=False, indent=2) if params else "(無參數資訊)"

        return (
            f"## 策略程式碼\n"
            f"```python\n{strategy_code}\n```\n\n"
            f"## 回測績效\n"
            + "\n".join(perf_lines)
            + f"\n\n## 現有參數\n```json\n{params_str}\n```\n\n"
            f"請提供 {max_suggestions} 種以上改善方案，"
            f"依 system prompt 格式輸出 JSON。"
        )

    # ── 內部：JSON 解析 ───────────────────────────────────────────────

    @staticmethod
    def _parse_result(raw: str, total_tokens: int) -> OptimizationResult:
        """嘗試解析 LLM 回傳的 JSON；失敗時以純文字 fallback。

        解析策略：
            1. 直接 json.loads(raw)
            2. 提取 ```json ... ``` 區塊
            3. 掃描第一個 { ... } 區塊
            4. Fallback：analysis=raw，suggestions=[]，parse_ok=False
        """
        # --- 嘗試 1：直接解析 ---
        data = _try_json(raw.strip())

        # --- 嘗試 2：```json 區塊 ---
        if data is None:
            m = re.search(r"```json\s*(.*?)```", raw, re.DOTALL | re.IGNORECASE)
            if m:
                data = _try_json(m.group(1).strip())

        # --- 嘗試 3：第一個 { } 區塊 ---
        if data is None:
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            if m:
                data = _try_json(m.group(0))

        # --- Fallback ---
        if data is None:
            logger.warning("AIOptimizer：無法解析 JSON，使用純文字 fallback")
            return OptimizationResult(
                analysis=raw,
                suggestions=[],
                raw_json=raw,
                total_tokens=total_tokens,
                parse_ok=False,
            )

        analysis    = data.get("analysis", "")
        suggestions = data.get("suggestions", [])

        # 確保 suggestions 是 list[dict]
        if not isinstance(suggestions, list):
            suggestions = []

        return OptimizationResult(
            analysis=analysis,
            suggestions=suggestions,
            raw_json=raw,
            total_tokens=total_tokens,
            parse_ok=True,
        )

    # ── 內部：載入 system prompt ──────────────────────────────────────

    @staticmethod
    def _load_system_prompt() -> str:
        if _TEMPLATE_PATH.exists():
            return _TEMPLATE_PATH.read_text(encoding="utf-8")
        logger.warning("找不到 optimize.md，使用內建預設 prompt")
        return (
            "你是台指期量化策略優化專家。"
            "請分析回測結果，找出虧損原因，提出 3 種以上具體改善方案。"
            "輸出格式：{\"analysis\": \"...\", \"suggestions\": [{\"title\": \"...\", "
            "\"description\": \"...\", \"new_params\": {}, \"expected_improvement\": \"...\"}]}"
        )


# ── 工具函式 ──────────────────────────────────────────────────────────

def _try_json(text: str) -> dict | None:
    """嘗試解析 JSON 字串，失敗回傳 None。"""
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, ValueError):
        pass
    return None
