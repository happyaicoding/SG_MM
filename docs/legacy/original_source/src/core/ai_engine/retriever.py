"""策略檢索器 — 接收策略需求查詢，回傳語意最相近的實戰策略 (top-K)。

整合三個組件：
    Embedder      — 把 query 轉向量
    VectorStore   — DuckDB VSS 做相似度搜尋
    StrategyLibrary — 從 content_json 還原 ProvenStrategy

D2 設計：先做 metadata 過濾（category），再做語意排序。

Usage:
    from src.core.ai_engine.retriever import StrategyRetriever

    retriever = StrategyRetriever()      # 自動載 embedder + vector_store
    examples = retriever.find_similar(
        category="trend",
        timeframe="15min",
        direction="both",
        extra_query="ATR stop loss intraday close",
        k=2,
    )
    for s in examples:
        print(s.name, s.timeframe)

    # 給 generator 用：直接拿 prompt-ready 文字
    prompt_block = retriever.find_similar_as_prompt(...)
"""
from __future__ import annotations

import logging
from pathlib import Path

from src.core.ai_engine.embedder import Embedder
from src.core.ai_engine.library import ProvenStrategy
from src.core.ai_engine.vector_store import VectorStore

logger = logging.getLogger(__name__)


class StrategyRetriever:
    """從向量庫檢索實戰策略當 LLM few-shot 範例。

    Args:
        embedder:    自訂 embedder（None = 用預設 BAAI/bge-m3）
        vector_store: 自訂 store（None = 用預設路徑 db/strategy_vectors.duckdb）
        lazy:        延遲初始化（embedder 載入慢，預設 True）
    """

    def __init__(
        self,
        embedder:     Embedder    | None = None,
        vector_store: VectorStore | None = None,
        lazy:         bool = True,
    ) -> None:
        self._embedder     = embedder
        self._vector_store = vector_store
        if not lazy:
            _ = self.embedder  # 觸發載入

    @property
    def embedder(self) -> Embedder:
        if self._embedder is None:
            self._embedder = Embedder()
        return self._embedder

    @property
    def vector_store(self) -> VectorStore:
        if self._vector_store is None:
            self._vector_store = VectorStore(dim=self.embedder.dim)
        return self._vector_store

    # ── 主要 API ──────────────────────────────────────────────────

    def find_similar(
        self,
        category:     str,
        holding_type: str = "",
        timeframe:    str = "",
        direction:    str = "both",
        extra_query:  str = "",
        k:            int = 2,
    ) -> list[ProvenStrategy]:
        """查詢語意最相近的策略（先 metadata 過濾，再語意排序）。

        Args:
            category:     小分類（trend / mean_reversion / ...）
            holding_type: 大分類（daytrade / swing），可選；指定可大幅縮小範圍
            timeframe:    週期（放入 query 文字提升匹配）
            direction:    方向（同上）
            extra_query:  補充描述（如「ATR stop loss」、「low volatility filter」）
            k:            回傳數量

        Returns:
            list[ProvenStrategy]，按相似度遞減；庫內無對應策略時回 []
        """
        # 庫為空 → 直接回 []，不用算 embedding
        ht = holding_type or None
        if self.vector_store.count(holding_type=ht, category=category) == 0:
            label = f"holding_type={holding_type or '*'}, category={category}"
            logger.info("library 中 %s 無策略可參考", label)
            return []

        # 組查詢文字（與 ProvenStrategy.to_embedding_text 同樣風格，提升匹配）
        query = self._build_query(category, holding_type, timeframe, direction, extra_query)
        logger.debug("retriever query: %s", query[:120].replace("\n", " "))

        query_vec = self.embedder.encode(query)
        results = self.vector_store.search(query_vec, k=k,
                                            holding_type=ht, category=category)

        # 還原 ProvenStrategy
        strategies: list[ProvenStrategy] = []
        for name, dist, content in results:
            try:
                # content_json 含所有 ProvenStrategy 欄位
                strategies.append(ProvenStrategy(**{
                    k_: v for k_, v in content.items()
                    if k_ in ProvenStrategy.__dataclass_fields__
                }))
                logger.debug("  hit: %s (cosine_dist=%.3f)", name, dist)
            except Exception as exc:
                logger.warning("還原 ProvenStrategy 失敗 %s：%s", name, exc)

        return strategies

    def find_similar_as_prompt(
        self,
        category:     str,
        holding_type: str = "",
        timeframe:    str = "",
        direction:    str = "both",
        extra_query:  str = "",
        k:            int = 2,
    ) -> str:
        """同 find_similar，但回傳 prompt-ready markdown 字串。

        無命中時回空字串（讓呼叫端可直接 if 判斷）。
        """
        examples = self.find_similar(category, holding_type, timeframe, direction,
                                     extra_query, k=k)
        if not examples:
            return ""

        header = (
            f"# 已上線實戰策略範例（請參考其風控、進出場結構，但不要直接抄襲）\n\n"
            f"以下 {len(examples)} 支策略是經過真實市場驗證的同類型範本。\n"
            f"請學習其中的：止損機制、時段過濾、訊號確認、訊號去抖動等專業設計。\n\n"
        )
        return header + "\n\n".join(s.to_prompt_block() for s in examples)

    # ── Helpers ───────────────────────────────────────────────────

    @staticmethod
    def _build_query(
        category:     str,
        holding_type: str,
        timeframe:    str,
        direction:    str,
        extra_query:  str,
    ) -> str:
        parts = [f"Category: {category}"]
        if holding_type:
            parts.append(f"Holding Type: {holding_type}")
        if timeframe:
            parts.append(f"Timeframe: {timeframe}")
        if direction:
            parts.append(f"Direction: {direction}")
        if extra_query:
            parts.append(f"Requirements: {extra_query}")
        return "\n".join(parts)

    def close(self) -> None:
        if self._vector_store is not None:
            self._vector_store.close()
