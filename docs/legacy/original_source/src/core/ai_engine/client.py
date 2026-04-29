"""LLM 客戶端封裝 — BaseLLMClient Protocol + 多供應商實作 + 工廠函式。

架構：
    BaseLLMClient    — typing.Protocol，定義最小介面（chat + total_tokens）
    ClaudeClient     — Anthropic Claude 實作，含 Retry / Rate Limit / Token 計數
    MiniMaxClient    — MiniMax（Anthropic 相容 API）實作
    create_llm_client() — 工廠函式，依 provider / model 建立對應客戶端

Usage:
    # 預設（從 config.yaml 讀取 provider + model）
    from src.core.ai_engine.client import create_llm_client
    client = create_llm_client()

    # 指定 Claude 模型
    client = create_llm_client("claude", model="claude-haiku-4-5")

    # MiniMax M2.7
    client = create_llm_client("minimax")
    client = create_llm_client("minimax", model="MiniMax-M2.7-highspeed")

    # 直接建立
    from src.core.ai_engine.client import ClaudeClient, MiniMaxClient
    client = ClaudeClient(model="claude-opus-4-5")
    client = MiniMaxClient(model="MiniMax-M2.7")

環境變數：
    ANTHROPIC_API_KEY  — Claude API Key
    MINIMAX_API_KEY    — MiniMax API Key

規範（CLAUDE.md）：
    Retry: 最多 3 次，指數退避 (1s/4s/16s)
    Rate Limit: 捕捉 429，等待 60s 後重試
"""
from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import anthropic
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# 預設模型常數
_CLAUDE_DEFAULT_MODEL   = "claude-sonnet-4-20250514"
_MINIMAX_DEFAULT_MODEL  = "MiniMax-M2.7"
_MINIMAX_BASE_URL       = "https://api.minimaxi.com/anthropic"

# 向下相容舊引用
MODEL   = _CLAUDE_DEFAULT_MODEL
_BACKOFF = [1, 4, 16]


# ── Protocol 介面 ─────────────────────────────────────────────────

@runtime_checkable
class BaseLLMClient(Protocol):
    """所有 LLM 客戶端必須實作的最小介面。

    任何類別只要擁有 `chat()` 方法與 `total_tokens` 屬性，
    即自動符合此 Protocol（duck typing，不需繼承）。
    """

    @property
    def total_tokens(self) -> int: ...

    def chat(
        self,
        messages: list[dict],
        system: str = "",
        max_tokens: int = 2048,
        temperature: float = 0.7,
        tools: list[dict] | None = None,
    ) -> str: ...


# ── Claude 實作 ───────────────────────────────────────────────────

class ClaudeClient:
    def __init__(self, model: str = MODEL) -> None:
        self._client = anthropic.Anthropic()
        self.model = model
        self._total_tokens = 0

    def chat(
        self,
        messages: list[dict],
        system: str = "",
        max_tokens: int = 2048,
        temperature: float = 0.7,
        tools: list[dict] | None = None,
    ) -> str:
        """送出對話請求，回傳 assistant 回覆文字。含 retry 與 rate limit 處理。"""
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools

        for attempt, wait in enumerate(_BACKOFF + [None], start=1):
            try:
                response = self._client.messages.create(**kwargs)
                self._total_tokens += response.usage.input_tokens + response.usage.output_tokens
                logger.debug(
                    "Claude API OK (attempt %d) | tokens: in=%d out=%d | total=%d",
                    attempt,
                    response.usage.input_tokens,
                    response.usage.output_tokens,
                    self._total_tokens,
                )
                return response.content[0].text

            except anthropic.RateLimitError:
                logger.warning("Rate limit hit — waiting 60s...")
                time.sleep(60)

            except anthropic.APIStatusError as exc:
                if wait is None:
                    raise
                logger.warning("API error (attempt %d): %s — retrying in %ss", attempt, exc, wait)
                time.sleep(wait)

        raise RuntimeError("Claude API 請求失敗，已超過最大重試次數")

    @property
    def total_tokens(self) -> int:
        return self._total_tokens


# ── MiniMax 實作 ──────────────────────────────────────────────────

class MiniMaxClient:
    """MiniMax LLM 客戶端（Anthropic 相容 API）。

    MiniMax 使用與 Anthropic SDK 相同的請求格式，只需替換 base_url 與 API Key。

    差異（相較於標準 Anthropic API）：
        - base_url : https://api.minimaxi.com/anthropic
        - API Key  : 環境變數 MINIMAX_API_KEY
        - temperature : 必須在 (0.0, 1.0]（不含 0.0），自動夾緊至 0.01
        - 不支援  : top_k、stop_sequences、image/document 輸入

    支援模型：
        MiniMax-M2.7（預設）、MiniMax-M2.7-highspeed
        MiniMax-M2.5、MiniMax-M2.5-highspeed
        MiniMax-M2.1、MiniMax-M2.1-highspeed、MiniMax-M2
    """

    def __init__(self, model: str = _MINIMAX_DEFAULT_MODEL) -> None:
        api_key = os.getenv("MINIMAX_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "找不到 MINIMAX_API_KEY 環境變數。"
                "請在 .env 加入：MINIMAX_API_KEY=your_key_here"
            )
        self._client = anthropic.Anthropic(
            api_key=api_key,
            base_url=_MINIMAX_BASE_URL,
        )
        self.model = model
        self._total_tokens = 0

    def chat(
        self,
        messages: list[dict],
        system: str = "",
        max_tokens: int = 2048,
        temperature: float = 0.7,
        tools: list[dict] | None = None,
    ) -> str:
        """送出對話請求，回傳 assistant 回覆文字。含 retry 與 rate limit 處理。

        Note:
            MiniMax temperature 範圍為 (0.0, 1.0]，傳入 0.0 會自動夾緊至 0.01。
        """
        # MiniMax 不接受 temperature=0.0，最小值為 0.01
        temperature = max(0.01, min(1.0, temperature))

        # M2.7 預設啟用 Thinking，thinking block 本身會消耗 token，
        # 需保留足夠空間給 text block，最低設為 1024
        if max_tokens < 1024:
            logger.debug("MiniMax max_tokens=%d 太小，自動調整為 1024", max_tokens)
            max_tokens = 1024

        kwargs: dict[str, Any] = {
            "model":      self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages":   messages,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools

        for attempt, wait in enumerate(_BACKOFF + [None], start=1):
            try:
                response = self._client.messages.create(**kwargs)
                self._total_tokens += (
                    response.usage.input_tokens + response.usage.output_tokens
                )
                logger.debug(
                    "MiniMax API OK (attempt %d) | tokens: in=%d out=%d | total=%d",
                    attempt,
                    response.usage.input_tokens,
                    response.usage.output_tokens,
                    self._total_tokens,
                )
                # 回應可能含 thinking block，取第一個 text block
                for block in response.content:
                    if block.type == "text":
                        return block.text
                raise RuntimeError(
                    f"MiniMax 回應中找不到 text block，"
                    f"實際 block 類型：{[b.type for b in response.content]}"
                )

            except anthropic.RateLimitError:
                logger.warning("MiniMax rate limit hit — waiting 60s...")
                time.sleep(60)

            except anthropic.APIStatusError as exc:
                if wait is None:
                    raise
                logger.warning(
                    "MiniMax API error (attempt %d): %s — retrying in %ss",
                    attempt, exc, wait,
                )
                time.sleep(wait)

        raise RuntimeError("MiniMax API 請求失敗，已超過最大重試次數")

    @property
    def total_tokens(self) -> int:
        return self._total_tokens


# ── NVIDIA NIM 實作 ───────────────────────────────────────────────

class NvidiaClient:
    """NVIDIA NIM API 客戶端（OpenAI 相容格式）。

    支援 Qwen3.5-122B 等 NIM 模型，含串流解析與 <think> 思考區塊剝離。

    環境變數：
        NVIDIA_API_KEY — NVIDIA NIM API Key（nvapi-...）

    支援模型（部分）：
        qwen/qwen3.5-122b-a10b（預設）
        meta/llama-3.3-70b-instruct
        mistralai/mixtral-8x22b-instruct-v0.1
    """

    _BASE_URL = "https://integrate.api.nvidia.com/v1/chat/completions"

    def __init__(
        self,
        model: str = "qwen/qwen3.5-122b-a10b",
        enable_thinking: bool = True,
    ) -> None:
        api_key = os.getenv("NVIDIA_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "找不到 NVIDIA_API_KEY 環境變數。"
                "請在 .env 加入：NVIDIA_API_KEY=nvapi-..."
            )
        self._api_key = api_key
        self.model = model
        self.enable_thinking = enable_thinking
        self._total_tokens = 0

    def chat(
        self,
        messages: list[dict],
        system: str = "",
        max_tokens: int = 2048,
        temperature: float = 0.7,
        tools: list[dict] | None = None,
    ) -> str:
        """送出串流請求，收集 delta.content（正式回覆），回傳純文字。

        NIM Qwen3.5 思考模式說明：
            - delta.reasoning / delta.reasoning_content → 思考過程（自動略過）
            - delta.content                             → 最終回覆（我們收集這個）
        因此不需要剝離 <think> 標籤，NIM 已在協議層分離兩個欄位。
        """
        import json

        oai_msgs: list[dict] = []
        if system:
            oai_msgs.append({"role": "system", "content": system})
        oai_msgs.extend(messages)

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        # thinking 模式需要大量 token 空間（思考本身會消耗 token）
        effective_max_tokens = max(8192, max_tokens) if self.enable_thinking else max(1024, max_tokens)
        payload: dict = {
            "model": self.model,
            "messages": oai_msgs,
            "max_tokens": effective_max_tokens,
            "temperature": max(0.01, min(1.0, temperature)),
            "top_p": 0.95,
            "stream": True,
            "stream_options": {"include_usage": True},  # 最後 chunk 含 usage
        }
        if self.enable_thinking:
            payload["chat_template_kwargs"] = {"enable_thinking": True}

        for attempt, wait in enumerate(_BACKOFF + [None], start=1):
            try:
                import httpx
                content_text = ""
                with httpx.Client(timeout=300) as client:
                    with client.stream(
                        "POST", self._BASE_URL,
                        headers=headers, json=payload,
                    ) as resp:
                        resp.raise_for_status()
                        for raw_line in resp.iter_lines():
                            line = raw_line.strip()
                            if not line or line == "data: [DONE]":
                                continue
                            if not line.startswith("data: "):
                                continue
                            try:
                                chunk = json.loads(line[6:])
                            except json.JSONDecodeError:
                                continue
                            choices = chunk.get("choices", [])
                            if choices:
                                delta = choices[0].get("delta", {})
                                # 只收集 content（正式回覆），略過 reasoning（思考過程）
                                content_text += delta.get("content") or ""
                            # include_usage=True 時最後一個 chunk choices=[] 但含 usage
                            if usage := chunk.get("usage"):
                                self._total_tokens += usage.get("total_tokens", 0)

                text = content_text.strip()
                if not text:
                    raise RuntimeError(
                        "NIM content 為空（思考尚未完成或 max_tokens 不足）"
                    )

                logger.debug(
                    "NVIDIA NIM OK (attempt %d) | total_tokens=%d",
                    attempt, self._total_tokens,
                )
                return text

            except Exception as exc:
                if wait is None:
                    raise
                logger.warning(
                    "NVIDIA NIM error (attempt %d): %s — retrying in %ss",
                    attempt, exc, wait,
                )
                time.sleep(wait)

        raise RuntimeError("NVIDIA NIM 請求失敗，已超過最大重試次數")

    @property
    def total_tokens(self) -> int:
        return self._total_tokens


# ── 工廠函式 ──────────────────────────────────────────────────────

_SUPPORTED_PROVIDERS = ("claude", "minimax", "nvidia")


def create_llm_client(
    provider: str | None = None,
    model: str | None = None,
    **kwargs,
) -> BaseLLMClient:
    """依 provider 建立對應的 LLM 客戶端。

    Args:
        provider: LLM 供應商（"claude" / "minimax"）；
                  None 時從 config.yaml llm.provider 讀取，預設 "claude"
        model:    模型名稱；None 時從 config.yaml 讀取對應 provider 的預設模型
        **kwargs: 額外傳給客戶端建構子的參數

    Returns:
        符合 BaseLLMClient Protocol 的客戶端實例

    Raises:
        ValueError: provider 不在支援清單中

    Examples:
        # 預設（從 config.yaml）
        client = create_llm_client()

        # Claude — 指定模型
        client = create_llm_client("claude", model="claude-haiku-4-5")

        # MiniMax M2.7（需 MINIMAX_API_KEY）
        client = create_llm_client("minimax")
        client = create_llm_client("minimax", model="MiniMax-M2.7-highspeed")
    """
    _provider = provider or _load_provider_from_config()

    if _provider == "claude":
        _model = model or _load_claude_model_from_config()
        return ClaudeClient(model=_model, **kwargs)

    if _provider == "minimax":
        _model = model or _load_minimax_model_from_config()
        return MiniMaxClient(model=_model, **kwargs)

    if _provider == "nvidia":
        _model = model or _load_config().get("nvidia", {}).get(
            "model", "qwen/qwen3.5-122b-a10b"
        )
        return NvidiaClient(model=_model, **kwargs)

    raise ValueError(
        f"不支援的 LLM provider：{_provider!r}。"
        f"目前支援：{', '.join(_SUPPORTED_PROVIDERS)}"
    )


# ── Config 讀取輔助 ───────────────────────────────────────────────

def _load_config() -> dict:
    """讀取 config.yaml，失敗時回傳空 dict。"""
    try:
        import yaml
        cfg_path = Path(__file__).resolve().parents[3] / "config.yaml"
        with open(cfg_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _load_provider_from_config() -> str:
    """從 config.yaml llm.provider 讀取；預設 'claude'。"""
    return _load_config().get("llm", {}).get("provider", "claude")


def _load_claude_model_from_config() -> str:
    """從 config.yaml claude.model 讀取；預設 _CLAUDE_DEFAULT_MODEL。"""
    return _load_config().get("claude", {}).get("model", _CLAUDE_DEFAULT_MODEL)


def _load_minimax_model_from_config() -> str:
    """從 config.yaml minimax.model 讀取；預設 _MINIMAX_DEFAULT_MODEL。"""
    return _load_config().get("minimax", {}).get("model", _MINIMAX_DEFAULT_MODEL)
