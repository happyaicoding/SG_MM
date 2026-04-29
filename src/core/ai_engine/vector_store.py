"""src/core/ai_engine/vector_store.py — DuckDB 向量庫存取層（V1.4 三向量 schema）。

依 rag_design.md §3：strategies_developed 為三向量（metadata/semantic/code），
其他 Collection 為單 semantic_vector。

Usage:
    from src.core.ai_engine.vector_store import VectorStore
    store = VectorStore()
    store.upsert_developed("strat_001", strategy_data, vectors)
    results = store.search_developed(query_vec, trading_session="daytrade_day")
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import duckdb
import numpy as np

logger = logging.getLogger(__name__)

_DEFAULT_DB = Path(__file__).resolve().parents[3] / "data" / "duckdb" / "strategy_vectors.duckdb"

_TABLES = {
    "developed": "strategies_developed",
    "ideas": "strategies_ideas",
    "failed": "strategies_failed",
    "web": "knowledge_web",
}


class VectorStore:
    """DuckDB VSS 向量倉（支援三向量 schema）。

    Args:
        db_path: 資料庫檔路徑，None 時使用預設路徑
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or _DEFAULT_DB
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(self.db_path))
        self._setup()

    def _setup(self) -> None:
        """安裝 / 載入 vss extension，建立所有 Collection 表（若不存在）。"""
        self._con.execute("INSTALL vss")
        self._con.execute("LOAD vss")

        # strategies_developed：三向量
        self._con.execute(f"""
            CREATE TABLE IF NOT EXISTS {_TABLES["developed"]} (
                id                VARCHAR PRIMARY KEY,
                trading_session   VARCHAR NOT NULL,
                logic_type        VARCHAR NOT NULL,
                timeframe         VARCHAR NOT NULL,
                direction         VARCHAR,
                metadata_vector   FLOAT[1024],
                semantic_vector   FLOAT[1024],
                code_vector       FLOAT[1024],
                summary           TEXT,
                description       TEXT,
                notes             TEXT,
                market_assumption TEXT,
                el_code           TEXT,
                yaml_content      TEXT,
                sharpe            REAL,
                max_drawdown      REAL,
                profit_factor     REAL,
                overfitting_flag  BOOLEAN DEFAULT FALSE,
                created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self._ensure_hnsw("developed", "metadata_vector")
        self._ensure_hnsw("developed", "semantic_vector")
        self._ensure_hnsw("developed", "code_vector")

        # strategies_ideas：單 semantic_vector
        self._con.execute(f"""
            CREATE TABLE IF NOT EXISTS {_TABLES["ideas"]} (
                id              VARCHAR PRIMARY KEY,
                source          TEXT,
                content         TEXT,
                semantic_vector FLOAT[1024],
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # strategies_failed：單 semantic_vector
        self._con.execute(f"""
            CREATE TABLE IF NOT EXISTS {_TABLES["failed"]} (
                id               VARCHAR PRIMARY KEY,
                what_was_tried   TEXT NOT NULL,
                why_failed       TEXT NOT NULL,
                failure_metrics  TEXT,
                semantic_vector  FLOAT[1024],
                created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self._ensure_hnsw("failed", "semantic_vector")

        # knowledge_web：單 semantic_vector
        self._con.execute(f"""
            CREATE TABLE IF NOT EXISTS {_TABLES["web"]} (
                id              VARCHAR PRIMARY KEY,
                url             VARCHAR UNIQUE,
                title           TEXT,
                content         TEXT,
                semantic_vector FLOAT[1024],
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self._ensure_hnsw("web", "semantic_vector")

    def _ensure_hnsw(self, collection: str, column: str) -> None:
        """確保 HNSW 索引存在（若不存在則建立）。"""
        idx_name = f"idx_{collection}_{column.replace('_vector', '')}"
        try:
            self._con.execute(f"""
                CREATE INDEX IF NOT EXISTS {idx_name}
                ON {_TABLES[collection]} ({column})
                USING HNSW (metric = 'cosine')
            """)
        except Exception:
            pass  # 索引已存在或建立失敗（不阻斷流程）

    # ── strategies_developed ─────────────────────────────────────────────

    def upsert_developed(
        self,
        id_: str,
        metadata: dict,
        vectors: dict,
    ) -> None:
        """寫入或更新一個已開發策略（三向量）。"""
        if any(v is not None and len(v) != 1024 for v in [vectors.get("metadata"), vectors.get("semantic"), vectors.get("code")] if v is not None):
            raise ValueError("向量維度必須為 1024")

        vec_meta = self._vec_to_list(vectors.get("metadata"))
        vec_sem = self._vec_to_list(vectors.get("semantic"))
        vec_code = self._vec_to_list(vectors.get("code"))
        now = datetime.now()

        self._con.execute(f"DELETE FROM {_TABLES['developed']} WHERE id = ?", [id_])
        self._con.execute(
            f"""INSERT INTO {_TABLES['developed']}
                (id, trading_session, logic_type, timeframe, direction,
                 metadata_vector, semantic_vector, code_vector,
                 summary, description, notes, market_assumption,
                 el_code, yaml_content, sharpe, max_drawdown, profit_factor,
                 overfitting_flag, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                id_,
                metadata.get("trading_session", ""),
                metadata.get("logic_type", ""),
                metadata.get("timeframe", ""),
                metadata.get("direction", ""),
                vec_meta,
                vec_sem,
                vec_code,
                metadata.get("summary", ""),
                metadata.get("description", ""),
                metadata.get("notes", ""),
                metadata.get("market_assumption", ""),
                metadata.get("el_code", ""),
                metadata.get("yaml_content", ""),
                metadata.get("sharpe"),
                metadata.get("max_drawdown"),
                metadata.get("profit_factor"),
                metadata.get("overfitting_flag", False),
                now,
            ],
        )

    def search_developed(
        self,
        query_vec: np.ndarray,
        k: int = 5,
        trading_session: str | None = None,
        logic_type: str | None = None,
        column: str = "semantic_vector",
    ) -> list[tuple[str, float, dict]]:
        """向量相似度檢索（cosine distance，越小越相似）。

        Args:
            query_vec: 查詢向量（已 L2 normalize）
            k: 回傳前 K 個
            trading_session: 大分類過濾（daytrade_day / swing_full 等）
            logic_type: 小分類過濾（trend / mean_reversion 等）
            column: 使用哪個向量檢索（metadata / semantic / code）

        Returns:
            list of (id, distance, metadata_dict)
        """
        if column not in ("metadata_vector", "semantic_vector", "code_vector"):
            raise ValueError(f"不支援的 column：{column}")

        vec_list = self._vec_to_list(query_vec)
        where_parts: list[str] = []
        params: list = [vec_list]

        if trading_session:
            where_parts.append("trading_session = ?")
            params.append(trading_session)
        if logic_type:
            where_parts.append("logic_type = ?")
            params.append(logic_type)

        where_clause = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
        params.append(k)

        sql = f"""
            SELECT id,
                   array_cosine_similarity({column}, CAST(? AS FLOAT[1024])) AS sim,
                   trading_session, logic_type, timeframe, direction,
                   summary, description, notes, el_code, yaml_content,
                   sharpe, max_drawdown, profit_factor
            FROM {_TABLES['developed']}
            {where_clause}
            ORDER BY sim DESC
            LIMIT ?
        """
        rows = self._con.execute(sql, params).fetchall()
        return [
            (
                r[0],
                float(r[1]),
                {
                    "trading_session": r[2],
                    "logic_type": r[3],
                    "timeframe": r[4],
                    "direction": r[5],
                    "summary": r[6],
                    "description": r[7],
                    "notes": r[8],
                    "el_code": r[9],
                    "yaml_content": r[10],
                    "sharpe": r[11],
                    "max_drawdown": r[12],
                    "profit_factor": r[13],
                },
            )
            for r in rows
        ]

    def upsert_failed(
        self,
        id_: str,
        what_was_tried: str,
        why_failed: str,
        failure_metrics: dict | None,
        semantic_vector: np.ndarray,
    ) -> None:
        """寫入失敗策略反例。"""
        vec_list = self._vec_to_list(semantic_vector)
        metrics_json = json.dumps(failure_metrics, ensure_ascii=False) if failure_metrics else ""

        self._con.execute(f"DELETE FROM {_TABLES['failed']} WHERE id = ?", [id_])
        self._con.execute(
            f"""INSERT INTO {_TABLES['failed']}
                (id, what_was_tried, why_failed, failure_metrics, semantic_vector)
                VALUES (?, ?, ?, ?, ?)""",
            [id_, what_was_tried, why_failed, metrics_json, vec_list],
        )

    def search_failed(
        self,
        query_vec: np.ndarray,
        k: int = 2,
    ) -> list[tuple[str, float, dict]]:
        """檢索失敗反例。"""
        vec_list = self._vec_to_list(query_vec)
        sql = f"""
            SELECT id,
                   array_cosine_similarity(semantic_vector, CAST(? AS FLOAT[1024])) AS sim,
                   what_was_tried, why_failed, failure_metrics
            FROM {_TABLES['failed']}
            ORDER BY sim DESC
            LIMIT ?
        """
        rows = self._con.execute(sql, [vec_list, k]).fetchall()
        return [
            (r[0], float(r[1]), {"what_was_tried": r[2], "why_failed": r[3], "failure_metrics": r[4]})
            for r in rows
        ]

    def upsert_idea(
        self,
        id_: str,
        content: str,
        semantic_vector: np.ndarray,
        source: str = "user_input",
    ) -> None:
        """寫入策略想法。"""
        vec_list = self._vec_to_list(semantic_vector)
        self._con.execute(f"DELETE FROM {_TABLES['ideas']} WHERE id = ?", [id_])
        self._con.execute(
            f"""INSERT INTO {_TABLES['ideas']} (id, source, content, semantic_vector)
                VALUES (?, ?, ?, ?)""",
            [id_, source, content, vec_list],
        )

    def count(self, collection: str = "developed") -> int:
        """回傳 Collection 筆數。"""
        row = self._con.execute(f"SELECT COUNT(*) FROM {_TABLES[collection]}").fetchone()
        return int(row[0]) if row else 0

    def clear(self, collection: str = "developed") -> None:
        """清空 Collection（用於重建索引）。"""
        self._con.execute(f"DELETE FROM {_TABLES[collection]}")
        logger.info("Collection '%s' 已清空", collection)

    # ── Resource management ───────────────────────────────────────────────────

    def close(self) -> None:
        self._con.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    @staticmethod
    def _vec_to_list(vec: np.ndarray | None) -> list[float] | None:
        if vec is None:
            return None
        return vec.astype(np.float32).flatten().tolist()
