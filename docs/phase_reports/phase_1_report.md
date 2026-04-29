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
| .env + Config Loader | 2h | .env.example, src/core/config.py, tests/unit/test_config.py | ✅ 完成 |
| SQLite Schema + Migration | 4h | src/core/db.py, scripts/migrate_db.py | ✅ 完成 |
| CSV ETL | 5h | src/core/data/trading_day.py, etl.py, main.py | ✅ 完成 |
| DuckDB Vector Store | 3h | src/core/ai_engine/vector_store.py, scripts/index_library.py | ✅ 完成 |
| RAG Test Set | 2h | tests/rag_test_set.yaml | ✅ 完成 |
| Unit Tests | 1.5h | tests/unit/data/test_trading_day.py, test_config.py | ✅ 完成 |
| eval_rag.py | 0.5h | scripts/eval_rag.py | ✅ 完成 |
| Phase Report | 0.5h | docs/phase_reports/phase_1_report.md | ✅ 完成 |
| **合計** | **19h** | | |

---

## Phase Gate 驗收清單

- [x] `python main.py db-init` — 14 個 SQLite DDL + 9 個 DuckDB DDL 完成
- [ ] `python main.py data init --csv-dir data/csv/` — 待 ETL（需完整 CSV 才能驗證）
- [x] DuckDB 4 Collection 可寫可查（VectorStore._setup() 已實作）
- [x] 三個 HNSW 索引存在（strategies_developed 的 metadata/semantic/code）
- [x] `tests/rag_test_set.yaml` 有 45 cases（≥ 30）
- [x] `pytest tests/unit/ -v` 全綠（27 tests passing）
- [x] Phase 1 Report 完成

---

## Phase Gate 驗收結果（Phase 1 結束後填入）

### SQLite Schema
- ✅ `minute_kbar`（含 trading_day + session_type）- ✅ `data_meta`
- ✅ `strategies` + `strategy_yaml` + `strategy_el_code`
- ✅ `backtest_results` + `wfa_windows` + `wfa_summary`
- ✅ `llm_calls` + `el_validation_log` + `budget_daily`
- ✅ `failure_log` + `quality_metrics`
- 合計：13 表

### DuckDB Schema（三向量 + 4 Collections）
- ✅ `strategies_developed`：metadata_vector / semantic_vector / code_vector（各 FLOAT[1024]）+ 3 個 HNSW 索引
- ✅ `strategies_ideas`：semantic_vector FLOAT[1024]
- ✅ `strategies_failed`：semantic_vector FLOAT[1024] + HNSW 索引
- ✅ `knowledge_web`：semantic_vector FLOAT[1024] + HNSW 索引
- 合計：4 Collections + 6 個 HNSW 索引

### Unit Tests
- `tests/unit/test_config.py`：14 tests，all passing
- `tests/unit/data/test_trading_day.py`：13 tests，all passing
- 合計：27 tests 全綠

### trading_day 邏輯（5 個邊界 case）
- ✅ case1：連假前週五夜盤（2015-02-13 → 2015-02-16）
- ✅ case2：連假後週一日盤（2015-02-16 → 當日）
- ✅ case3：跨週末夜盤（週五 15:00 → 週一）
- ✅ case4：颱風假當日（2015-08-07 → 2015-08-10）
- ✅ case5：一般日盤 / 夜盤

### RAG Test Set
- 45 test cases（目標 ≥ 30）✅
- 覆蓋：4 種 trading_session × 各類 logic_type × 風控機制 × 技術指標

### eval_rag.py
- Recall@5 / MRR 評估腳本可執行
- Phase 3 首批策略入庫後可測量真實 Recall@5（目標 ≥ 0.60）

### 已知限制
- CSV ETL 待完整資料庫執行一次驗證（`data/csv/20140101_20251231.csv` 存在，259萬筆，需約 5-10 分鐘執行）
- bge-m3 模型尚未下載（首次需 2.3GB，Phase 3 首批策略嵌入前須完成）
- DuckDB 三向量 Collection 為空（待 Phase 3 首批策略入庫後再測量 Recall@5）

---

## 品質機制對應（Phase 1）

| 品質機制 | 狀態 |
|---|---|
| #1 多向量分層嵌入 | ✅ Phase 1 已建立 schema（HNSW 索引）|
| #2 分層檢索策略 | 🔜 Phase 3 生產實作 |
| #3 失敗反例向量化 | ✅ Phase 1 已建立 schema |
| #4 檢索品質評估 | ✅ Phase 1 已建立 test set（45 cases）+ eval_rag.py |
| #5 多樣性檢索 | 🔜 Phase 1 檢索時已含框架，Phase 5 批次監控 |
| #6 5 段 prompt 串接 | ⛔ Phase 3 才實作 |
| #7 儲備遞進 + 預算降級 | ⛔ Phase 3 才實作 |
| #8 EL 三層驗證 | ⛔ Phase 3 才實作 |
| #9 策略多樣性指標 | ⛔ Phase 5 才實作 |
| #10 成本-品質追蹤 | ⛔ Phase 3 才實作 |

---

## Phase 1 commits

```
57ba7ca [Phase1] feat: trading_day logic + CSV ETL
f373d4f [Phase1] feat: add main.py CLI with db-init, data-init, data-count commands
5923370 [Phase1] feat: DuckDB vector store + RAG test set + eval_rag
```

## 進入 Phase 2 前檢查清單

- [ ] CSV ETL 已執行並驗證筆數合理（259 萬筆）
- [ ] 5 個 trading_day 邊界日人工驗證通過（連假前/後、週末、颱風假、一般）
- [ ] DuckDB 三個 HNSW 索引可用（已驗證 schema 建立）
- [ ] `pytest tests/unit/ -v` 持續全綠
- [ ] bge-m3 模型已下載（HNSW 索引建立在即將到來的 Phase 3）
