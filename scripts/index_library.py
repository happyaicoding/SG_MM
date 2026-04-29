"""scripts/index_library.py — 重建策略向量索引

掃描 library/ → 嵌入 (BAAI/bge-m3) → 寫入 db/strategy_vectors.duckdb

執行時機：
    - 第一次設定 library 後
    - 每次新增、修改、刪除 library/ 內的 .els / .yaml 後

執行方式：
    python scripts/index_library.py                 # 增量更新（upsert）
    python scripts/index_library.py --rebuild       # 完全重建（先清空）
    python scripts/index_library.py --dry-run       # 只列出要處理的檔，不實際寫入

注意：
    - 第一次跑會自動下載 BAAI/bge-m3 模型 (~2.3GB) 到 ~/.cache/huggingface/
    - 後續跑只需幾秒（每支策略嵌入約 0.1 秒）
"""
from __future__ import annotations

import sys
import time
import logging
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%H:%M:%S",
)

from src.core.ai_engine.embedder     import Embedder
from src.core.ai_engine.library      import StrategyLibrary
from src.core.ai_engine.vector_store import VectorStore


def main() -> int:
    parser = argparse.ArgumentParser(description="重建 AISMART 策略向量索引")
    parser.add_argument("--rebuild", action="store_true",
                        help="先清空整張表再重建（預設為增量 upsert）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只列出將處理的檔案，不實際嵌入或寫入")
    parser.add_argument("--library-root", default=None,
                        help="自訂 library/ 根目錄路徑")
    args = parser.parse_args()

    print("=" * 60)
    print("AISMART — 策略向量索引建立")
    print(f"  rebuild={args.rebuild}  dry_run={args.dry_run}")
    print("=" * 60)

    # Step 1：掃描檔案
    lib_root = Path(args.library_root) if args.library_root else None
    library = StrategyLibrary(root=lib_root)
    strategies = library.load_all()
    print(f"\n[STEP 1/3] 掃描 library/")
    print(f"  根目錄：{library.root}")
    print(f"  找到 {len(strategies)} 支策略：")
    for s in strategies:
        print(f"    - [{s.holding_type:8s}/{s.category:14s}] "
              f"{s.name:30s} ({s.timeframe})")

    if not strategies:
        print(f"\n[STOP] library/ 內無策略檔可索引。")
        print(f"        請依 library/README.md 加入 .els + .yaml 後再執行。")
        return 0

    if args.dry_run:
        print(f"\n[STOP] --dry-run：不實際嵌入或寫入")
        return 0

    # Step 2：載入 embedder（會下載模型）
    print(f"\n[STEP 2/3] 載入 embedding 模型 BAAI/bge-m3")
    print(f"  （第一次執行會下載 ~2.3GB 到 ~/.cache/huggingface/，請稍候...）")
    t0 = time.time()
    embedder = Embedder()
    print(f"  模型載入完成（{time.time()-t0:.1f} 秒），維度={embedder.dim}")

    # Step 3：嵌入並寫入 VectorStore
    print(f"\n[STEP 3/3] 計算嵌入並寫入向量庫")
    store = VectorStore(dim=embedder.dim)

    if args.rebuild:
        print(f"  --rebuild：清空既有資料")
        store.clear()

    # 批次嵌入（一次處理所有，更快）
    texts = [s.to_embedding_text() for s in strategies]
    t0 = time.time()
    vectors = embedder.encode(texts)
    print(f"  嵌入完成（{time.time()-t0:.1f} 秒，{len(strategies)} 支）")

    for s, vec in zip(strategies, vectors):
        store.upsert(
            name         = s.name,
            holding_type = s.holding_type,
            category     = s.category,
            timeframe    = s.timeframe,
            direction    = s.direction,
            embedding    = vec,
            content      = s.to_dict(),
        )
        print(f"  [OK] {s.name} [{s.holding_type}/{s.category}/{s.timeframe}]")

    total = store.count()
    store.close()

    print(f"\n{'=' * 60}")
    print(f"[完成] 向量庫共 {total} 支策略可供 LLM 檢索")
    print(f"  資料庫：db/strategy_vectors.duckdb")
    print(f"{'=' * 60}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
