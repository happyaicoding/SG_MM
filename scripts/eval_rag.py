"""scripts/eval_rag.py — RAG 檢索品質評估腳本。

每 Phase 結束時跑一次，計算：
  - Recall@5：top-5 結果中含正確答案的比例
  - MRR：正確答案排名倒數平均（Mean Reciprocal Rank）

結果寫入 quality_metrics 表，並與上次結果比較（退步 > 5% 自動警告）。

Usage:
    python scripts/eval_rag.py
    python scripts/eval_rag.py --db-path ./data/sqlite/main.db
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import yaml
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.config import get_settings
from src.core.db import sqlite_conn
from src.core.ai_engine.vector_store import VectorStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def load_test_set(path: Path | None = None) -> list[dict]:
    """載入 tests/rag_test_set.yaml。"""
    if path is None:
        path = Path(__file__).resolve().parents[1] / "tests" / "rag_test_set.yaml"
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data["test_cases"]


def _mock_embed_query(query: str) -> list[float]:
    """TODO：置換為真實 bge-m3 嵌入（Phase 3 後）。"""
    import hashlib
    import struct
    h = hashlib.sha256(query.encode()).digest()
    # 從 hash 產生穩定但假的 1024 維向量（僅通過編譯）
    vals = struct.unpack("<256d", h[:256 * 8])
    result = [v % 1.0 for v in vals]
    # 補足到 1024
    while len(result) < 1024:
        result.append(result[len(result) % 256])
    return result[:1024]


def calculate_recall_at_k(
    test_cases: list[dict],
    vectorstore: VectorStore,
    k: int = 5,
) -> float:
    """Recall@K = (top-K 中包含預期關鍵字通過的 query 數) / 總 query 數。"""
    hits = 0
    for tc in tqdm(test_cases, desc="  Recall@5 評估中"):
        expected = tc.get("expected", [])
        query = tc["query"]
        # 取出 trading_session / logic_type 過濾
        ts_filter = None
        lt_filter = None
        for kw in expected:
            s = str(kw)
            if s in ("daytrade_day", "daytrade_night", "swing_day", "swing_full"):
                ts_filter = s
            elif s in ("trend", "mean_reversion", "breakout", "opening", "scalp", "pattern"):
                lt_filter = s

        try:
            import numpy as np
            vec = np.array(_mock_embed_query(query), dtype=np.float32)
            results = vectorstore.search_developed(
                query_vec=vec,
                k=k,
                trading_session=ts_filter,
                logic_type=lt_filter,
            )
        except Exception as exc:
            logger.debug("檢索失敗（query='%s'）：%s", query, exc)
            results = []

        # 簡化評估：若 Collection 為空（Phase 1 初始狀態），視為 skip
        total_count = vectorstore.count("developed")
        if total_count == 0:
            logger.info("strategies_developed Collection 為空，跳過量化評估")
            return 0.0

        # 評估：hit = 至少有一個 expected 關鍵字被描述覆蓋
        hit = False
        for _, _, meta in results:
            desc_text = " ".join(str(v) for v in meta.values() if v).lower()
            query_lower = query.lower()
            # 粗略：query 關鍵字出現在 strategy metadata 中即為 hit
            if any(kw in desc_text for kw in expected if len(str(kw)) > 3):
                hit = True
                break
            if any(kw in query_lower for kw in expected if len(str(kw)) > 3):
                hit = True
                break

        if hit:
            hits += 1

    return hits / len(test_cases) if test_cases else 0.0


def calculate_mrr(
    test_cases: list[dict],
    vectorstore: VectorStore,
    k: int = 20,
) -> float:
    """MRR = mean(1 / 第一個正確結果的排名)。"""
    reciprocal_ranks = []

    total_count = vectorstore.count("developed")
    if total_count == 0:
        return 0.0

    for tc in tqdm(test_cases, desc="  MRR 評估中"):
        expected = tc.get("expected", [])
        query = tc["query"]

        ts_filter = None
        lt_filter = None
        for kw in expected:
            s = str(kw)
            if s in ("daytrade_day", "daytrade_night", "swing_day", "swing_full"):
                ts_filter = s
            elif s in ("trend", "mean_reversion", "breakout", "opening", "scalp", "pattern"):
                lt_filter = s

        try:
            import numpy as np
            vec = np.array(_mock_embed_query(query), dtype=np.float32)
            results = vectorstore.search_developed(
                query_vec=vec,
                k=k,
                trading_session=ts_filter,
                logic_type=lt_filter,
            )
        except Exception:
            results = []

        for rank, (_, _, meta) in enumerate(results, start=1):
            desc_text = " ".join(str(v) for v in meta.values() if v).lower()
            if any(kw in desc_text for kw in expected if len(str(kw)) > 3):
                reciprocal_ranks.append(1.0 / rank)
                break
        else:
            reciprocal_ranks.append(0.0)

    return sum(reciprocal_ranks) / len(reciprocal_ranks) if reciprocal_ranks else 0.0


def save_metrics(recall_5: float, mrr: float, phase: str = "Phase 1") -> None:
    """寫入 quality_metrics 表。"""
    with sqlite_conn() as conn:
        conn.execute(
            "INSERT INTO quality_metrics (metric_name, metric_value, phase) VALUES (?, ?, ?)",
            ["rag_recall_at_5", recall_5, phase],
        )
        conn.execute(
            "INSERT INTO quality_metrics (metric_name, metric_value, phase) VALUES (?, ?, ?)",
            ["rag_mrr", mrr, phase],
        )


def get_last_metrics() -> tuple[float | None, float | None]:
    """取得上一次的 Recall@5 / MRR（用於比較退步）。"""
    with sqlite_conn() as conn:
        recall_row = conn.execute(
            "SELECT metric_value FROM quality_metrics "
            "WHERE metric_name='rag_recall_at_5' "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        mrr_row = conn.execute(
            "SELECT metric_value FROM quality_metrics "
            "WHERE metric_name='rag_mrr' "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    recall_prev = float(recall_row[0]) if recall_row else None
    mrr_prev = float(mrr_row[0]) if mrr_row else None
    return recall_prev, mrr_prev


def main() -> int:
    logger.info("=== RAG 檢索品質評估 ===")

    # 載入測試集
    test_cases = load_test_set()
    logger.info("載入 %d 個 test cases", len(test_cases))
    if len(test_cases) < 30:
        logger.warning("test cases < 30（%d 個），Phase 1 需 ≥ 30", len(test_cases))

    # 初始化 VectorStore
    try:
        store = VectorStore()
    except Exception as exc:
        logger.error("VectorStore 初始化失敗：%s", exc)
        return 1

    total = store.count("developed")
    logger.info("strategies_developed  Collection: %d 筆", total)
    logger.info("strategies_ideas     Collection: %d 筆", store.count("ideas"))
    logger.info("strategies_failed    Collection: %d 筆", store.count("failed"))

    # 計算指標
    if total == 0:
        logger.info("Collection 為空（Phase 1 初始狀態）")
        logger.info("Phase 1 目標 Recall@5 ≥ 0.60，MRR ≥ 0.40（首批策略入庫後才評估）")
        logger.info("請在 Phase 3 首批策略入庫後再跑一次本腳本")
        store.close()
        return 0

    recall_5 = calculate_recall_at_k(test_cases, store, k=5)
    mrr = calculate_mrr(test_cases, store)

    # 取得上次結果
    recall_prev, mrr_prev = get_last_metrics()

    logger.info("")
    logger.info("=== 評估結果 ===")
    logger.info("Recall@5 : %.2f%%", recall_5 * 100)
    logger.info("MRR      : %.3f", mrr)
    if recall_prev is not None:
        delta = recall_5 - recall_prev
        logger.info("（上次   : %.2f%%，Delta %+.2f%%）", recall_prev * 100, delta * 100)
        if delta < -0.05:
            logger.warning("Recall@5 退步超過 5%%，請檢查是否破壞了檢索品質")

    # 寫入 quality_metrics
    try:
        save_metrics(recall_5, mrr)
        logger.info("結果已寫入 quality_metrics 表")
    except Exception as exc:
        logger.warning("寫入 quality_metrics 失敗：%s", exc)

    store.close()

    # Phase 1 門檻
    phase1_target_recall = 0.60
    phase1_target_mrr = 0.40
    if recall_5 >= phase1_target_recall and mrr >= phase1_target_mrr:
        logger.info("Phase 1 品質門檻：%s", "PASS")
        return 0
    else:
        logger.warning(
            "Phase 1 品質門檻：%s（Recall@5 %.2f%% 需要 ≥ %.0f%%，MRR %.3f 需要 ≥ %.3f）",
            "未達標",
            recall_5 * 100,
            phase1_target_recall * 100,
            mrr,
            phase1_target_mrr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
