"""DuckDB VSS 向量資料庫 — 策略向量的存取層。

Schema：
    strategy_vectors (
        name         VARCHAR PRIMARY KEY,   -- 策略英文名（library 內唯一）
        holding_type VARCHAR,                -- 大分類：daytrade / swing
        category     VARCHAR,                -- 小分類：trend / mean_reversion / ...
        timeframe    VARCHAR,                -- 1min / 15min / 1D
        direction    VARCHAR,                -- both / long / short
        embedding    FLOAT[<dim>],           -- 嵌入向量（normalize 後 cosine 用）
        content_json VARCHAR,                -- 完整 ProvenStrategy 序列化（JSON）
        indexed_at   TIMESTAMP                -- 索引時間
    )

特性：
    - 用 cosine distance 做相似度搜尋（搭配 normalize=True 的 embedder）
    - 支援 metadata 過濾（D2 設計：先過濾 category，再做語意排序）
    - 維度由 embedder.dim 動態決定（換模型不需改 schema）

Usage:
    from src.core.ai_engine.vector_store import VectorStore

    store = VectorStore()  # 自動建表
    store.upsert("MA_Cross", "trend", "5min", "both", vec, content_dict)
    results = store.search(query_vec, k=2, category="trend")
    # → [(name, distance, content_dict), ...]
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import duckdb
import numpy as np

logger = logging.getLogger(__name__)

_PROJECT_ROOT  = Path(__file__).resolve().parents[3]
_DEFAULT_DB    = _PROJECT_ROOT / "db" / "strategy_vectors.duckdb"
_TABLE         = "strategy_vectors"


class VectorStore:
    """DuckDB VSS 向量倉。

    Args:
        db_path: 資料庫檔路徑
        dim:     向量維度（必須與 embedder.dim 一致）
    """

    def __init__(
        self,
        db_path: Path | None = None,
        dim:     int = 1024,
    ) -> None:
        self.db_path = db_path or _DEFAULT_DB
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.dim = dim
        self._con = duckdb.connect(str(self.db_path))
        self._setup()

    def _setup(self) -> None:
        """安裝 / 載入 VSS extension，建表（若不存在）。

        若舊版 schema（無 holding_type 欄位）存在 → 自動 ALTER 升級。
        """
        self._con.execute("INSTALL vss")
        self._con.execute("LOAD vss")
        self._con.execute(f"""
            CREATE TABLE IF NOT EXISTS {_TABLE} (
                name         VARCHAR PRIMARY KEY,
                holding_type VARCHAR,
                category     VARCHAR,
                timeframe    VARCHAR,
                direction    VARCHAR,
                embedding    FLOAT[{self.dim}],
                content_json VARCHAR,
                indexed_at   TIMESTAMP
            )
        """)
        # 舊版 schema 升級（從 0 → 加 holding_type 欄位）
        cols = [r[0] for r in self._con.execute(
            f"SELECT column_name FROM information_schema.columns "
            f"WHERE table_name = '{_TABLE}'"
        ).fetchall()]
        if "holding_type" not in cols:
            logger.warning(
                "偵測到舊版 schema（無 holding_type）→ 升級中，"
                "舊資料會以 'daytrade' 為預設值"
            )
            self._con.execute(
                f"ALTER TABLE {_TABLE} ADD COLUMN holding_type VARCHAR DEFAULT 'daytrade'"
            )

    # ── 寫入 ─────────────────────────────────────────────────────

    def upsert(
        self,
        name:         str,
        holding_type: str,
        category:     str,
        timeframe:    str,
        direction:    str,
        embedding:    np.ndarray,
        content:      dict,
    ) -> None:
        """寫入或更新一筆策略向量。"""
        if embedding.shape[-1] != self.dim:
            raise ValueError(
                f"向量維度不符：embedder 給 {embedding.shape[-1]}，"
                f"但 store 是 {self.dim}。請重建資料庫或統一模型。"
            )
        vec_list = embedding.astype(np.float32).tolist()
        content_json = json.dumps(content, ensure_ascii=False)
        now = datetime.now()

        # DuckDB 沒有原生 UPSERT，用 DELETE + INSERT
        self._con.execute(f"DELETE FROM {_TABLE} WHERE name = ?", [name])
        self._con.execute(
            f"""INSERT INTO {_TABLE}
                (name, holding_type, category, timeframe, direction,
                 embedding, content_json, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [name, holding_type, category, timeframe, direction,
             vec_list, content_json, now],
        )

    def clear(self) -> None:
        """清空整張表（用於完全重建索引）。"""
        self._con.execute(f"DELETE FROM {_TABLE}")
        logger.info("VectorStore 已清空")

    # ── 查詢 ─────────────────────────────────────────────────────

    def search(
        self,
        query_vec:    np.ndarray,
        k:            int = 2,
        holding_type: str | None = None,
        category:     str | None = None,
    ) -> list[tuple[str, float, dict]]:
        """向量相似度檢索（cosine distance，越小越相似）。

        Args:
            query_vec:    查詢向量（已 L2 normalize）
            k:            回傳前 K 個
            holding_type: 大分類過濾（daytrade / swing），可選
            category:     小分類過濾（trend / mean_reversion / ...），可選

        Returns:
            list of (name, distance, content_dict)，依 distance 升冪
        """
        vec_list = query_vec.astype(np.float32).tolist()
        # 組 WHERE：可選 metadata 過濾，先濾再算距離
        where_parts: list[str] = []
        params: list = [vec_list]
        if holding_type:
            where_parts.append("holding_type = ?")
            params.append(holding_type)
        if category:
            where_parts.append("category = ?")
            params.append(category)
        where_clause = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
        params.append(k)

        sql = f"""
            SELECT name,
                   array_cosine_distance(embedding, CAST(? AS FLOAT[{self.dim}])) AS dist,
                   content_json
            FROM {_TABLE}
            {where_clause}
            ORDER BY dist ASC
            LIMIT ?
        """
        rows = self._con.execute(sql, params).fetchall()
        return [(name, float(dist), json.loads(content)) for name, dist, content in rows]

    def count(
        self,
        holding_type: str | None = None,
        category:     str | None = None,
    ) -> int:
        """回傳資料表筆數，可選 holding_type / category 過濾。"""
        where_parts: list[str] = []
        params: list = []
        if holding_type:
            where_parts.append("holding_type = ?")
            params.append(holding_type)
        if category:
            where_parts.append("category = ?")
            params.append(category)
        where_clause = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

        row = self._con.execute(
            f"SELECT COUNT(*) FROM {_TABLE} {where_clause}", params
        ).fetchone()
        return int(row[0]) if row else 0

    def list_all(self) -> list[dict]:
        """列出所有策略 metadata（不含 embedding，用於 debug）。"""
        rows = self._con.execute(
            f"SELECT name, holding_type, category, timeframe, direction, indexed_at "
            f"FROM {_TABLE} ORDER BY holding_type, category, name"
        ).fetchall()
        return [
            {"name": r[0], "holding_type": r[1], "category": r[2],
             "timeframe": r[3], "direction": r[4], "indexed_at": r[5]}
            for r in rows
        ]

    # ── Resource management ───────────────────────────────────────

    def close(self) -> None:
        self._con.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
