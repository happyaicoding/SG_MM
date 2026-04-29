# Phase 1 工作說明書（基礎設施與資料層）

## Phase 1 入口檢查（quality_safeguards.md §1.1）

- [x] 我已讀完 docs/quality_safeguards.md §1
- [x] 本 Phase 涵蓋的品質機制：#1 多向量分層（DuckDB 三向量 schema 建立）、#3 失敗反例 schema（strategies_failed Collection）、#4 RAG test set 建立（≥ 30 cases）
- [x] 本 Phase 不該動的品質機制：#6 5 段 prompt 串接、#7 儲備遞進 + 預算降級、#8 EL 三層驗證（均屬 Phase 3 實作）
- [x] 本 Phase 結束前必須確認的品質指標：DuckDB 三個 HNSW 索引存在、tests/rag_test_set.yaml ≥ 30 cases、scripts/eval_rag.py 可獨立執行

## 我的承諾

我在本 Phase 內：
- 不會合併 5 段 prompt 為單一大 prompt
- 不會用混合嵌入取代多向量分層
- 不會跳過 EL 三層驗證的任一層（Phase 3 才實作）
- 不會關閉預算控制機制（Phase 3 才實作）
- 不會把失敗策略丟掉不存
- 每完成一個模組都會寫 unit test

---

## Phase 1 模組清單

| 模組 | 工時 | 檔案 | 狀態 |
|---|---|---|---|
| .env + Config Loader | 2h | .env.example, src/core/config.py, tests/unit/test_config.py | 進行中 |
| SQLite Schema + Migration | 4h | src/core/db.py, scripts/migrate_db.py | 待辦 |
| CSV ETL | 5h | src/core/data/trading_day.py, etl.py, main.py | 待辦 |
| DuckDB Vector Store | 3h | src/core/ai_engine/vector_store.py, scripts/index_library.py | 待辦 |
| RAG Test Set | 2h | tests/rag_test_set.yaml | 待辦 |
| Unit Tests | 1.5h | tests/unit/data/test_trading_day.py, test_etl.py | 待辦 |
| eval_rag.py | 0.5h | scripts/eval_rag.py | 待辦 |
| Phase Report | 0.5h | docs/phase_reports/phase_1_report.md | 進行中 |
| **合計** | **19h** | | |

---

## Phase Gate 驗收清單

- [ ] `python main.py db init` — 13 個 SQLite 表 + 4 個 DuckDB Collection 建立
- [ ] `python main.py data init data/csv/` — CSV ETL 完成，筆數合理（約 259 萬根 12 年）
- [ ] 5 個 trading_day 邊界日期人工驗證通過
- [ ] DuckDB 三個 HNSW 索引存在（strategies_developed 的 metadata/semantic/code）
- [ ] `tests/rag_test_set.yaml` ≥ 30 cases
- [ ] `python scripts/eval_rag.py` Recall@5 ≥ 0.60
- [ ] `pytest tests/unit/ -v` 全綠
- [ ] phase_1_report.md 填完驗收結果

---

## Phase Gate 驗收結果（Phase 1 結束後填入）

待填入。
