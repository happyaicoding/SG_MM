"""並行多供應商策略生成器 — 同時用三個 LLM 生成策略，取最快或全部收集。

Usage:
    from src.core.ai_engine.parallel_generator import MultiProviderGenerator

    mpg = MultiProviderGenerator()

    # 三個 LLM 同時生成，回傳所有成功結果
    results = mpg.generate_all(
        strategy_type="trend",
        direction="both",
        holding_period="intraday",
        research_summary="台指期近期趨勢明顯...",
        existing_names=["MA_Cross"],
    )
    for r in results:
        print(r)   # ProviderResult(provider=..., success=..., ...)

    # 只等最快完成的那個
    winner = mpg.generate_first(...)
    print(winner)

設計重點：
- 每個 provider 各自建立獨立的 LLM client + StrategyGenerator
- 以 ThreadPoolExecutor 並行執行（I/O bound，適合多執行緒）
- existing_names 防止命名衝突：執行前合併 + 執行後動態追加
- 任一 provider 失敗不影響其他 provider
- 支援自訂 providers 清單（預設 claude / minimax / nvidia）
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any

from src.core.ai_engine.client import create_llm_client
from src.core.ai_engine.generator import GeneratedStrategy, StrategyGenerator

logger = logging.getLogger(__name__)

# 預設支援的供應商（依執行順序無關）
DEFAULT_PROVIDERS = ("claude", "minimax", "nvidia")


@dataclass
class ProviderResult:
    """單一 provider 的生成結果。"""
    provider: str
    success: bool
    result: GeneratedStrategy | None = None
    error: str = ""
    tokens: int = 0
    elapsed_sec: float = 0.0

    def __str__(self) -> str:
        if self.success and self.result:
            return (
                f"[{self.provider}] ✅  {self.result.name}"
                f"  tokens={self.tokens}  {self.elapsed_sec:.1f}s"
                f"  → {self.result.filepath}"
            )
        return f"[{self.provider}] ❌  {self.error}"


class MultiProviderGenerator:
    """同時驅動多個 LLM 供應商生成策略。

    Args:
        providers: 要啟用的供應商清單，預設 ("claude", "minimax", "nvidia")
        timeout:   每個 provider 的超時秒數（None = 不限制）
    """

    def __init__(
        self,
        providers: tuple[str, ...] = DEFAULT_PROVIDERS,
        timeout: float | None = 300.0,
    ) -> None:
        self._providers = providers
        self._timeout = timeout
        # 用 Lock 保護 existing_names 的動態追加
        self._names_lock = threading.Lock()

    # ── 公開介面 ──────────────────────────────────────────────────────────

    def generate_all(
        self,
        strategy_type: str,
        direction: str = "both",
        holding_period: str = "intraday",
        research_summary: str = "",
        existing_names: list[str] | None = None,
        save: bool = True,
        *,
        providers: tuple[str, ...] | None = None,
    ) -> list[ProviderResult]:
        """並行呼叫所有 provider，等待全部完成後回傳結果清單。

        Args:
            strategy_type:    策略類型（trend / mean_reversion / breakout）
            direction:        交易方向（long / short / both）
            holding_period:   持倉週期（intraday / swing / position）
            research_summary: 研究摘要（餵給 LLM 的市場背景）
            existing_names:   已存在的策略名稱（防止重複命名）
            save:             是否儲存至 src/strategies/generated/
            providers:        臨時覆蓋供應商清單

        Returns:
            list[ProviderResult]，成功 + 失敗都包含在內，依完成順序排列
        """
        active = providers or self._providers
        # 執行期間動態追加已產出的策略名稱，避免並行時重名
        live_names: list[str] = list(existing_names or [])

        results: list[ProviderResult] = []

        logger.info("MultiProviderGenerator: 並行啟動 %d 個 provider %s", len(active), active)

        with ThreadPoolExecutor(max_workers=len(active), thread_name_prefix="mpgen") as pool:
            future_map: dict[Future[ProviderResult], str] = {
                pool.submit(
                    self._run_one,
                    provider=p,
                    strategy_type=strategy_type,
                    direction=direction,
                    holding_period=holding_period,
                    research_summary=research_summary,
                    live_names=live_names,
                    save=save,
                ): p
                for p in active
            }

            for future in as_completed(future_map, timeout=self._timeout):
                provider = future_map[future]
                try:
                    pr = future.result()
                except Exception as exc:
                    pr = ProviderResult(provider=provider, success=False, error=str(exc))

                results.append(pr)
                logger.info("%s", pr)

        # 依 provider 原始順序重新排列（方便閱讀）
        order = {p: i for i, p in enumerate(active)}
        results.sort(key=lambda r: order.get(r.provider, 99))
        return results

    def generate_first(
        self,
        strategy_type: str,
        direction: str = "both",
        holding_period: str = "intraday",
        research_summary: str = "",
        existing_names: list[str] | None = None,
        save: bool = True,
        *,
        providers: tuple[str, ...] | None = None,
    ) -> ProviderResult:
        """並行呼叫所有 provider，回傳「第一個成功」的結果。

        其餘 provider 的 Future 在背景繼續執行但結果被捨棄。
        適合需要最快回應速度的場景。

        Returns:
            第一個成功的 ProviderResult；若全部失敗，回傳最後一個失敗結果。
        """
        active = providers or self._providers
        live_names: list[str] = list(existing_names or [])

        logger.info("MultiProviderGenerator.generate_first: 競速模式，provider=%s", active)

        last_failure: ProviderResult | None = None

        with ThreadPoolExecutor(max_workers=len(active), thread_name_prefix="mpgen_race") as pool:
            future_map: dict[Future[ProviderResult], str] = {
                pool.submit(
                    self._run_one,
                    provider=p,
                    strategy_type=strategy_type,
                    direction=direction,
                    holding_period=holding_period,
                    research_summary=research_summary,
                    live_names=live_names,
                    save=save,
                ): p
                for p in active
            }

            for future in as_completed(future_map, timeout=self._timeout):
                provider = future_map[future]
                try:
                    pr = future.result()
                except Exception as exc:
                    pr = ProviderResult(provider=provider, success=False, error=str(exc))

                if pr.success:
                    logger.info("競速勝出 → %s", pr)
                    # 取消尚未執行的任務（已在執行中的無法中斷，但不影響正確性）
                    for f in future_map:
                        f.cancel()
                    return pr
                else:
                    last_failure = pr
                    logger.warning("provider %s 失敗：%s", provider, pr.error)

        # 全部失敗
        return last_failure or ProviderResult(
            provider="none", success=False, error="所有 provider 均失敗"
        )

    # ── 內部：單一 provider 執行 ──────────────────────────────────────────

    def _run_one(
        self,
        *,
        provider: str,
        strategy_type: str,
        direction: str,
        holding_period: str,
        research_summary: str,
        live_names: list[str],
        save: bool,
    ) -> ProviderResult:
        """在獨立執行緒中建立 client + generator，執行生成。"""
        import time
        t0 = time.perf_counter()

        try:
            client = create_llm_client(provider)
            gen = StrategyGenerator(client=client)

            # 讀取當前已知名稱（執行緒安全快照）
            with self._names_lock:
                snapshot = list(live_names)

            result: GeneratedStrategy = gen.generate(
                strategy_type=strategy_type,
                direction=direction,
                holding_period=holding_period,
                research_summary=research_summary,
                existing_names=snapshot,
                save=save,
            )

            # 成功後把新名稱追加進共享清單，防止其他 provider 重名
            with self._names_lock:
                if result.name not in live_names:
                    live_names.append(result.name)

            elapsed = time.perf_counter() - t0
            return ProviderResult(
                provider=provider,
                success=True,
                result=result,
                tokens=client.total_tokens,
                elapsed_sec=elapsed,
            )

        except Exception as exc:
            elapsed = time.perf_counter() - t0
            logger.warning("[%s] 生成失敗（%.1fs）：%s", provider, elapsed, exc)
            return ProviderResult(
                provider=provider,
                success=False,
                error=str(exc),
                elapsed_sec=elapsed,
            )
