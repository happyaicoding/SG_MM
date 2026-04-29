"""AI 策略生成器 — 呼叫 Claude API 根據 prompt template 生成新 Python 策略。

完整流程：
    1. 載入 prompt_templates/generate.md 作為 system prompt
    2. 組裝 user message（策略類型、方向、參考策略、研究摘要）
    3. 呼叫 ClaudeClient.chat()（max_tokens=4096）
    4. 從回應中萃取 Python 程式碼區塊
    5. 語法驗證（compile + 介面檢查）
    6. 最多重試 3 次（每次將錯誤訊息回饋給 Claude）
    7. 儲存至 src/strategies/generated/<NAME>.py
    8. 回傳 GeneratedStrategy dataclass

Usage:
    from src.core.ai_engine.generator import StrategyGenerator

    gen = StrategyGenerator()
    result = gen.generate(
        strategy_type="trend",
        direction="both",
        holding_period="intraday",
        research_summary="近期台指期日盤出現明顯趨勢行情...",
        existing_names=["MA_Cross", "RSI_Reversal"],
    )
    print(result.name)     # 策略 NAME
    print(result.code)     # 完整 Python 程式碼
    print(result.filepath) # 儲存路徑
"""
from __future__ import annotations

import ast
import logging
import re
import textwrap
from dataclasses import dataclass
from pathlib import Path

from src.core.ai_engine.client import BaseLLMClient, create_llm_client

logger = logging.getLogger(__name__)

_TEMPLATE_PATH    = Path(__file__).parent / "prompt_templates" / "generate.md"
_EL_TEMPLATE_PATH = Path(__file__).parent / "prompt_templates" / "generate_el.md"
_GENERATED_DIR    = Path(__file__).resolve().parents[3] / "src" / "strategies" / "generated"

_MAX_RETRIES = 3


def _strip_non_ascii_comments(code: str) -> str:
    """移除 EasyLanguage 程式碼中含非 ASCII 字元的注釋行。

    PLEditor 以 cp950（Windows ANSI）讀取 .el 檔案。
    若注釋行含 UTF-8 中文字元，PLEditor 解碼失敗，整個檔案顯示為空白。
    此函式保留程式邏輯行，只移除有問題的注釋。

    Args:
        code: EasyLanguage 原始碼字串

    Returns:
        僅含 ASCII 字元的清洗後程式碼
    """
    cleaned = []
    for line in code.splitlines():
        stripped = line.strip()
        # 純注釋行（// 開頭）且含非 ASCII → 移除
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
    return "\r\n".join(cleaned)  # PLEditor 慣用 CRLF


@dataclass
class GeneratedStrategy:
    """AI 生成策略的結果。"""
    name:     str            # 策略 NAME（如 "MACD_Divergence"）
    category: str            # 小分類：trend / mean_reversion / ...
    holding_type: str        # 大分類：daytrade / swing
    timeframe: str           # 策略適用週期（如 "15min"）
    code:     str            # 完整 Python 原始碼
    filepath: Path | None    # Python 儲存路徑（若已存檔）
    prompt_summary: str      # 生成時的 user prompt 摘要
    total_tokens: int        # 本次消耗的 token 數
    el_code:     str | None = None   # EasyLanguage 原始碼（generate_el() 後填入）
    el_filepath: Path | None = None  # .el 儲存路徑


class StrategyGenerator:
    """透過 Claude API 生成新的 Python 交易策略。

    Args:
        client: ClaudeClient 實例（不提供則自動建立）
    """

    def __init__(self, client: BaseLLMClient | None = None) -> None:
        self._client = client or create_llm_client()
        self._system_prompt = self._load_system_prompt()

    # ── 主要入口 ──────────────────────────────────────────────────

    def generate(
        self,
        strategy_type: str = "trend",
        direction: str = "both",
        holding_period: str = "intraday",
        holding_type: str = "daytrade",
        research_summary: str = "",
        existing_names: list[str] | None = None,
        save: bool = True,
        with_el: bool = False,
        fail_patterns: str = "",
        confirmed_examples: list[dict] | None = None,
        library_prompt: str = "",
    ) -> GeneratedStrategy:
        """生成一個新的 Python 策略，並可選同時轉換為 EasyLanguage。

        Args:
            strategy_type:    小分類（CATEGORY）：trend / mean_reversion / opening /
                              scalp / swing / pattern
            direction:        方向：long / short / both
            holding_period:   （legacy）持倉週期描述
            holding_type:     大分類（HOLDING_TYPE）：daytrade（當沖）/ swing（波段）
            research_summary: 市場研究摘要（可空）
            existing_names:   已有策略名稱清單（避免重複）
            save:             是否儲存至 src/strategies/generated/
            with_el:          True 時同時呼叫 generate_el() 產生 EL 版本
            fail_patterns:    過往失敗教訓 markdown（反面教材，append 到 system prompt）
            confirmed_examples: 已通過初篩的範例 [{name, category, code}, ...]（中信任度）
            library_prompt:   實戰策略參考 markdown（高信任度，由 retriever 預先 format）

        Returns:
            GeneratedStrategy（with_el=True 時 el_code / el_filepath 已填入）
        """
        user_msg = self._build_user_message(
            strategy_type, direction, holding_period, holding_type,
            research_summary, existing_names or [],
            confirmed_examples or [],
            library_prompt,
        )
        prompt_summary = (
            f"type={strategy_type} direction={direction} "
            f"holding={holding_period}"
        )

        # 將反面教材 append 到 system prompt（不影響原 template）
        effective_system = self._system_prompt
        if fail_patterns:
            effective_system = (
                f"{self._system_prompt}\n\n"
                f"---\n\n{fail_patterns}"
            )

        tokens_before = self._client.total_tokens
        code = self._generate_with_retry(user_msg, system_prompt=effective_system)
        tokens_used = self._client.total_tokens - tokens_before

        name, category = self._extract_name_category(code)
        timeframe    = self._extract_timeframe(code)
        holding_type = self._extract_holding_type(code)

        filepath = None
        if save:
            filepath = self._save_code(name, code)

        logger.info(
            "策略生成完成：%s [%s/%s/%s]  tokens=%d  path=%s",
            name, holding_type, category, timeframe, tokens_used, filepath,
        )

        result = GeneratedStrategy(
            name=name,
            category=category,
            holding_type=holding_type,
            timeframe=timeframe,
            code=code,
            filepath=filepath,
            prompt_summary=prompt_summary,
            total_tokens=tokens_used,
        )

        if with_el:
            result = self.generate_el(result, save=save)

        return result

    # ── EasyLanguage 轉換 ─────────────────────────────────────────

    def generate_el(
        self,
        strategy: "GeneratedStrategy",
        save: bool = True,
    ) -> "GeneratedStrategy":
        """將已有的 Python 策略轉換為 EasyLanguage（Power Language）。

        使用 LLM 根據 Python 程式碼生成語意等價的 EL 程式碼。
        MC12 可直接編譯並執行此 EL 程式碼進行精測回測。

        Args:
            strategy: 已生成的 Python 策略（GeneratedStrategy）
            save:     True 時同時儲存為 <name>.el 檔

        Returns:
            更新後的 GeneratedStrategy（el_code / el_filepath 已填入）
        """
        el_system = self._load_el_system_prompt()
        user_msg = (
            f"## Python 策略原始碼\n\n```python\n{strategy.code}\n```\n\n"
            "請將上方 Python 策略完整轉換為 MultiCharts EasyLanguage。\n"
            "只輸出 ```easylanguage ... ``` 程式碼區塊。"
        )

        logger.info("呼叫 LLM 生成 EL 版本：%s", strategy.name)
        raw = self._client.chat(
            messages=[{"role": "user", "content": user_msg}],
            system=el_system,
            max_tokens=2048,
            temperature=0.2,   # EL 轉換需要精確，低 temperature
        )

        el_code = self._extract_el_code_block(raw)
        if not el_code:
            # fallback：嘗試直接用 raw 全文（有時 LLM 不加 fence）
            el_code = raw.strip()
            logger.warning("[EL] 未找到 ```easylanguage 區塊，使用原始回應")

        el_filepath = None
        if save:
            el_filepath = self._save_el(
                strategy.name, el_code, strategy.timeframe, strategy.holding_type
            )

        logger.info("EL 轉換完成：%s [%s/%s]  path=%s",
                    strategy.name, strategy.holding_type, strategy.timeframe, el_filepath)

        # 回傳更新後的 dataclass（dataclass 是 mutable，直接 replace）
        from dataclasses import replace
        return replace(strategy, el_code=el_code, el_filepath=el_filepath)

    # ── 內部方法 ──────────────────────────────────────────────────

    def _load_system_prompt(self) -> str:
        """載入 generate.md 作為 system prompt。"""
        if not _TEMPLATE_PATH.exists():
            raise FileNotFoundError(
                f"找不到 prompt template：{_TEMPLATE_PATH}"
            )
        return _TEMPLATE_PATH.read_text(encoding="utf-8")

    def _load_el_system_prompt(self) -> str:
        """載入 generate_el.md 作為 EL 轉換 system prompt。"""
        if not _EL_TEMPLATE_PATH.exists():
            raise FileNotFoundError(
                f"找不到 EL prompt template：{_EL_TEMPLATE_PATH}"
            )
        return _EL_TEMPLATE_PATH.read_text(encoding="utf-8")

    @staticmethod
    def _extract_el_code_block(text: str) -> str:
        """從回應中萃取 ```easylanguage ... ``` 或 ``` ... ``` 區塊。"""
        # 優先匹配 easylanguage fence
        for fence in (r"```easylanguage\s*\n(.*?)```",
                      r"```powerlanguage\s*\n(.*?)```",
                      r"```el\s*\n(.*?)```",
                      r"```\s*\n(.*?)```"):
            m = re.search(fence, text, re.DOTALL | re.IGNORECASE)
            if m:
                return textwrap.dedent(m.group(1)).strip()
        return ""

    def _save_el(
        self,
        name:         str,
        el_code:      str,
        timeframe:    str = "1min",
        holding_type: str = "daytrade",
    ) -> Path:
        """儲存 PLA 程式碼至 src/strategies/generated/<name>.el。

        重要：PLEditor 以 cp950（Windows ANSI）讀取來源檔，
        必須確保檔案內容為純 ASCII，不可含 UTF-8 多位元組字元。

        Header（Timeframe / HoldingType）由本函式強制注入，覆蓋 LLM 可能寫錯的版本，
        確保與 Python 策略類別屬性一致。
        """
        _GENERATED_DIR.mkdir(parents=True, exist_ok=True)
        filename = re.sub(r"[^a-zA-Z0-9_]", "_", name).lower() + ".el"
        el_path = _GENERATED_DIR / filename
        py_name = filename.replace(".el", ".py")
        header = (
            f"// AISMART Strategy: {name}\n"
            f"// Auto-generated by AISMART StrategyGenerator (do not edit manually)\n"
            f"// Python source: {py_name}\n"
            f"// HoldingType: {holding_type}\n"
            f"// Timeframe: {timeframe}\n\n"
        )
        # 確保 EL 程式碼內無非 ASCII 字元（LLM 有時摻雜中文注釋）
        clean_code = _strip_non_ascii_comments(el_code)
        # 移除 LLM 自己寫的 // Timeframe: / // HoldingType: 行避免重複（不分大小寫）
        clean_code = re.sub(
            r"^\s*//\s*(Timeframe|HoldingType)\s*:.*?\r?\n",
            "",
            clean_code,
            flags=re.MULTILINE | re.IGNORECASE,
        )
        # 寫入 cp950 — PLEditor 在 Windows 繁中系統的預設讀取編碼
        el_path.write_bytes((header + clean_code).encode("cp950", errors="replace"))
        logger.info("PLA 已儲存（holding=%s, timeframe=%s）：%s",
                    holding_type, timeframe, el_path)
        return el_path

    def _build_user_message(
        self,
        strategy_type: str,
        direction: str,
        holding_period: str,
        holding_type: str,
        research_summary: str,
        existing_names: list[str],
        confirmed_examples: list[dict] | None = None,
        library_prompt: str = "",
    ) -> str:
        """組裝 user message。"""
        parts = [
            "## 策略需求",
            f"- 大分類（HOLDING_TYPE）：{holding_type}"
            f"（daytrade=當沖當日平倉 / swing=波段跨日持有）",
            f"- 小分類（CATEGORY）：{strategy_type}",
            f"- 方向：{direction}（long=只做多 / short=只做空 / both=多空皆做）",
            f"- 持倉週期描述：{holding_period}",
        ]
        if existing_names:
            parts.append(f"- 已有策略（NAME 請勿重複）：{', '.join(existing_names)}")
        if research_summary:
            parts += ["", "## 市場研究摘要 / 上次失敗回饋", research_summary]
        # 高信任度：實戰策略（library/）— 優先放，引導 LLM 主要參考
        if library_prompt:
            parts += ["", library_prompt]
        # 中信任度：confirmed/ 通過初篩的策略
        if confirmed_examples:
            parts += ["", "## 通過初篩的策略範例（中等信任度，可參考結構）"]
            for ex in confirmed_examples:
                parts.append(
                    f"\n### {ex.get('name', '?')} [{ex.get('category', '?')}]\n"
                    f"```python\n{ex.get('code', '').strip()}\n```"
                )
        parts += [
            "",
            "請依照 system prompt 規範，輸出完整 Python 策略類別。",
            "只輸出程式碼區塊，不要任何說明文字。",
        ]
        return "\n".join(parts)

    def _generate_with_retry(
        self,
        user_msg: str,
        system_prompt: str | None = None,
    ) -> str:
        """呼叫 Claude API，最多重試 _MAX_RETRIES 次。

        每次失敗將錯誤訊息附加給 Claude 自我修正。
        """
        messages: list[dict] = [{"role": "user", "content": user_msg}]
        last_error: str = ""
        sys_prompt = system_prompt or self._system_prompt

        for attempt in range(1, _MAX_RETRIES + 1):
            if attempt > 1 and last_error:
                # 將前次回應與錯誤一起回饋
                messages.append({
                    "role": "user",
                    "content": (
                        f"上方程式碼有以下問題，請修正後重新輸出完整程式碼：\n\n{last_error}"
                    ),
                })

            logger.info("呼叫 Claude API 生成策略（第 %d/%d 次）...", attempt, _MAX_RETRIES)
            raw = self._client.chat(
                messages=messages,
                system=sys_prompt,
                max_tokens=4096,
                temperature=0.7,
            )
            # 加入 assistant 回應到對話歷史
            messages.append({"role": "assistant", "content": raw})

            code, error = self._extract_and_validate(raw)
            if code:
                return code

            last_error = error
            logger.warning("第 %d 次生成失敗：%s", attempt, error)

        raise RuntimeError(
            f"Claude 連續 {_MAX_RETRIES} 次生成無效策略，最後錯誤：{last_error}"
        )

    def _extract_and_validate(self, raw: str) -> tuple[str, str]:
        """從 Claude 回應中萃取程式碼並驗證。

        Returns:
            (code, error)：code 不為空表示成功；error 為失敗原因
        """
        code = self._extract_code_block(raw)
        if not code:
            return "", "回應中找不到 ```python ... ``` 程式碼區塊"

        # 語法驗證
        try:
            ast.parse(code)
        except SyntaxError as exc:
            return "", f"語法錯誤：{exc}"

        # 介面驗證
        error = self._validate_interface(code)
        if error:
            return "", error

        return code, ""

    @staticmethod
    def _extract_code_block(text: str) -> str:
        """從回應中萃取第一個 ```python ... ``` 區塊。"""
        pattern = r"```(?:python)?\s*\n(.*?)```"
        match = re.search(pattern, text, re.DOTALL)
        if match:
            return textwrap.dedent(match.group(1)).strip()
        return ""

    @staticmethod
    def _validate_interface(code: str) -> str:
        """檢查程式碼是否符合 BaseStrategy 介面規範。

        Returns:
            錯誤訊息；空字串表示通過
        """
        required = {
            "BaseStrategy": "缺少繼承 BaseStrategy",
            "NAME"        : "缺少 NAME 類別屬性",
            "PARAMS"      : "缺少 PARAMS 類別屬性",
            "CATEGORY"    : "缺少 CATEGORY 類別屬性",
            "HOLDING_TYPE": "缺少 HOLDING_TYPE 類別屬性（daytrade / swing）",
            "TIMEFRAME"   : "缺少 TIMEFRAME 類別屬性（如 TIMEFRAME = \"15min\"）",
            "generate_signals": "缺少 generate_signals() 方法",
            "metadata"    : "缺少 metadata() 方法",
            "validate_params": "缺少 validate_params() 方法",
            "shift(1)"    : "generate_signals() 需要呼叫 shift(1) 避免 lookahead bias",
        }
        errors = [msg for token, msg in required.items() if token not in code]
        return "；".join(errors) if errors else ""

    @staticmethod
    def _extract_name_category(code: str) -> tuple[str, str]:
        """從程式碼中解析 NAME 與 CATEGORY。"""
        name = "Unknown"
        category = "unknown"

        m = re.search(r'NAME\s*=\s*["\']([^"\']+)["\']', code)
        if m:
            name = m.group(1)

        m = re.search(r'CATEGORY\s*=\s*["\']([^"\']+)["\']', code)
        if m:
            category = m.group(1)

        return name, category

    @staticmethod
    def _extract_timeframe(code: str) -> str:
        """從 Python 程式碼中解析 TIMEFRAME 類別屬性，預設 1min。"""
        m = re.search(r'TIMEFRAME\s*=\s*["\']([^"\']+)["\']', code)
        return m.group(1) if m else "1min"

    @staticmethod
    def _extract_holding_type(code: str) -> str:
        """從 Python 程式碼中解析 HOLDING_TYPE 類別屬性，預設 daytrade。"""
        m = re.search(r'HOLDING_TYPE\s*=\s*["\']([^"\']+)["\']', code)
        return m.group(1) if m else "daytrade"

    def _save_code(self, name: str, code: str) -> Path:
        """儲存策略原始碼至 src/strategies/generated/<name>.py。"""
        _GENERATED_DIR.mkdir(parents=True, exist_ok=True)

        # 將 NAME 轉為合法檔名（小寫、底線）
        filename = re.sub(r"[^a-zA-Z0-9_]", "_", name).lower() + ".py"
        filepath = _GENERATED_DIR / filename

        header = (
            f'"""AI 自動生成策略：{name}。\n'
            f'由 StrategyGenerator 產生，請勿手動修改。\n'
            f'"""\n'
        )
        filepath.write_text(header + code, encoding="utf-8")
        logger.info("策略已儲存：%s", filepath)
        return filepath
