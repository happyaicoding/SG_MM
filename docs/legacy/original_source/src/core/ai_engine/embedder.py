"""語意嵌入器 — 將文字（策略元資料 + EL 程式碼）轉成向量。

預設使用 BAAI/bge-m3（多語言 + code-aware），1024 維輸出。

特性：
    - 多語言（中英文混雜的策略描述都能對齊）
    - 對 code 有不錯理解（雖非 code-specific 模型）
    - 完全本地執行（你的策略不會上傳任何外部 API）
    - L2-normalize 後，cosine 距離 = 0 ~ 2

模型首次使用會自動從 Hugging Face 下載到 ~/.cache/huggingface/（約 2.3GB）。
之後本地讀取，無網路需求。

Usage:
    from src.core.ai_engine.embedder import Embedder

    emb = Embedder()  # 第一次會下載模型
    vec = emb.encode("trend strategy with ATR stop")           # → np.ndarray (1024,)
    vecs = emb.encode(["text1", "text2"])                       # → np.ndarray (2, 1024)

    # 切換更小的模型（disk space 不夠時）
    emb = Embedder(model_name="paraphrase-multilingual-MiniLM-L12-v2")  # 470MB, 384 維
"""
from __future__ import annotations

import logging
from typing import overload

import numpy as np

logger = logging.getLogger(__name__)


# 預設模型（多語言 + 中等 code 理解）
_DEFAULT_MODEL = "BAAI/bge-m3"
_DEFAULT_DIM   = 1024   # bge-m3 dense vector 維度


class Embedder:
    """sentence-transformers 包裝器，預設 BAAI/bge-m3。

    Args:
        model_name: Hugging Face 模型 ID 或本地路徑
        device:     "cpu" / "cuda" / None（自動偵測）
        normalize:  是否 L2 normalize（預設 True，配合 cosine distance）
    """

    def __init__(
        self,
        model_name: str = _DEFAULT_MODEL,
        device:     str | None = None,
        normalize:  bool = True,
    ) -> None:
        # 延遲 import：避免未安裝 sentence-transformers 時整個 ai_engine 都崩
        from sentence_transformers import SentenceTransformer

        logger.info("載入 embedding 模型：%s（首次會下載到本地快取）", model_name)
        self._model = SentenceTransformer(model_name, device=device)
        self._model_name = model_name
        self._normalize  = normalize
        self._dim: int | None = None

    @property
    def dim(self) -> int:
        """向量維度（首次呼叫 encode 後快取）。"""
        if self._dim is None:
            # 用 dummy text 探測維度（避免硬編碼）
            self._dim = int(self.encode("probe").shape[-1])
        return self._dim

    @property
    def model_name(self) -> str:
        return self._model_name

    @overload
    def encode(self, text: str) -> np.ndarray: ...
    @overload
    def encode(self, text: list[str]) -> np.ndarray: ...

    def encode(self, text):
        """將文字編碼為向量。

        Args:
            text: 單一字串或字串 list

        Returns:
            單一字串輸入 → shape (D,)
            list 輸入     → shape (N, D)
        """
        is_single = isinstance(text, str)
        inputs = [text] if is_single else list(text)

        vectors = self._model.encode(
            inputs,
            normalize_embeddings=self._normalize,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        # 確保 float32（DuckDB VSS 要求）
        vectors = vectors.astype(np.float32, copy=False)

        return vectors[0] if is_single else vectors
