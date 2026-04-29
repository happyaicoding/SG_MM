# RAG 多向量檢索設計

> **文件用途**：DuckDB 向量庫設計、多向量分層嵌入、分層檢索、多樣性過濾、檢索品質評估
> **語言**：中文
> **適用對象**：Claude Code 在 Phase 1（建構）與 Phase 3（檢索）必讀

---

## 1. 設計目的

### 1.1 為什麼需要這份文件

舊專案的 RAG 設計問題（直接影響策略品質）：

```
舊版 library.py 的 to_embedding_text()：
  把 metadata + description + EL 程式碼**全部混在一段**嵌入

問題：
  ❌ 嵌入向量被「文字長度大的部分」（EL 程式碼）主導
  ❌ 中文查詢 → 跟英文程式碼算相似度，不準
  ❌ 無法針對「結構化欄位」做精確過濾
  ❌ 沒有失敗反例參與檢索
  ❌ 沒有多樣性保護（每次撈出來都是相似的策略）
  ❌ 沒有檢索品質評估機制
```

V1.4 的對應強化見下文。

### 1.2 V1.4 RAG 的 5 大支柱

1. **多向量分層**：每個策略產 3 個向量（metadata / semantic / code）
2. **分層檢索**：SQL 過濾 → 粗篩 → 精篩 → 多樣性
3. **失敗反例向量化**：strategies_failed 也參與 RAG
4. **檢索品質評估**：Recall@5、MRR 持續追蹤
5. **多樣性檢索**：排除過於相似的範例

---

## 2. 技術選型

### 2.1 向量資料庫：DuckDB

| 比較項 | DuckDB（V1.4 選擇）| ChromaDB（早期討論）|
|---|---|---|
| 部署 | 單檔，零部署 | 要跑容器或服務 |
| SQL 查詢 | 完整支援 | 不支援 |
| 結構化過濾 + 向量檢索同一句 SQL | ✅ | ❌ |
| 與 SQLite 並存 | ✅ 同進程 | ❌ 跨進程 |
| 向量索引 | HNSW（0.10+ 內建） | HNSW |
| 沿用舊專案 | ✅ | ❌ |

### 2.2 嵌入模型：BAAI/bge-m3

| 比較項 | bge-m3（V1.4 選擇）| OpenAI text-embedding-3-small |
|---|---|---|
| 中文表現 | 極好（中文是強項）| 一般 |
| 成本 | 零 | 按 token 計費 |
| 隱私 | 完全本地 | 資料送 API |
| 維度 | 1024 | 1536 |
| 首次成本 | 下載 2.3GB | 0 |
| 速度 | 無網路延遲 | 視網速 |
| 沿用舊專案 | ✅ | ❌ |

---

## 3. 多向量分層嵌入（核心品質機制 #1）

### 3.1 三向量定義

每個策略產出 **3 個獨立的向量**，分別存：

| 向量名稱 | 來源文字 | 字數限制 | 主要用途 |
|---|---|---|---|
| `metadata_vector` | trading_session + logic_type + timeframe + direction + 簡短描述 | 200 字內 | 粗篩（找同類型策略） |
| `semantic_vector` | description + notes + market_assumption | 500 字內 | 精篩（找語意相似的策略） |
| `code_vector` | EL 程式碼（截短或片段化） | — | 程式碼相似度（給 LLM 看實作參考）|

### 3.2 為什麼要分層

#### ❌ 舊專案做法（混合嵌入）的問題

```python
# 舊版（不可重蹈）
embedding_text = (
    f"Name: {self.name}\n"
    f"Type: {self.holding_type}\n"
    f"Description: {self.description}\n"   # 200 字中文
    f"--- EL Code ---\n"
    f"{self.el_code}"                       # 2000 字英文程式碼
)
```

**問題**：嵌入結果被 EL 程式碼主導，中文查詢算相似度時不準。

#### ✅ V1.4 三向量做法

```python
def generate_three_vectors(strategy: Strategy) -> ThreeVectors:
    """
    產生三個獨立向量。每個向量只嵌入「同質性高」的內容。
    """
    # 1. metadata_vector：結構化資訊 + 簡短一行描述
    metadata_text = (
        f"類型: {strategy.trading_session} {strategy.logic_type}\n"
        f"時間框架: {strategy.timeframe}\n"
        f"方向: {strategy.direction}\n"
        f"概要: {strategy.summary}"  # 50 字內
    )
    
    # 2. semantic_vector：純中文語意
    semantic_text = (
        f"{strategy.description}\n"
        f"設計理念: {strategy.market_assumption}\n"
        f"備註: {strategy.notes}"
    )
    
    # 3. code_vector：純 EL 程式碼
    code_text = strategy.el_code  # 完整或截短到 token 上限
    
    return ThreeVectors(
        metadata=embedder.encode(metadata_text),
        semantic=embedder.encode(semantic_text),
        code=embedder.encode(code_text)
    )
```

### 3.3 DuckDB Schema

```sql
CREATE TABLE strategies_developed (
    id VARCHAR PRIMARY KEY,
    name VARCHAR NOT NULL,
    
    -- 結構化欄位（用於 SQL 過濾）
    trading_session VARCHAR NOT NULL,
    logic_type VARCHAR NOT NULL,
    timeframe VARCHAR NOT NULL,
    direction VARCHAR,
    
    -- 三向量（V1.4 核心）
    metadata_vector FLOAT[1024],
    semantic_vector FLOAT[1024],
    code_vector FLOAT[1024],
    
    -- 內容
    summary TEXT,                       -- 50 字內
    description TEXT,
    notes TEXT,
    market_assumption TEXT,
    el_code TEXT,
    yaml_content TEXT,
    
    -- 績效
    sharpe REAL,
    max_drawdown REAL,
    profit_factor REAL,
    overfitting_flag BOOLEAN DEFAULT FALSE,
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- HNSW 索引（每個向量獨立索引）
CREATE INDEX idx_dev_metadata ON strategies_developed
    USING HNSW (metadata_vector) WITH (metric = 'cosine');
CREATE INDEX idx_dev_semantic ON strategies_developed
    USING HNSW (semantic_vector) WITH (metric = 'cosine');
CREATE INDEX idx_dev_code ON strategies_developed
    USING HNSW (code_vector) WITH (metric = 'cosine');
```

---

## 4. 分層檢索策略（核心品質機制 #2）

### 4.1 五步驟檢索

```
Step 1: SQL 過濾結構化欄位
   ↓ 留下符合 trading_session + logic_type 的候選

Step 2: metadata_vector 第一輪檢索（粗篩）
   ↓ 返回 top-20 候選

Step 3: semantic_vector 第二輪檢索（精篩）
   ↓ 返回 top-5 最相關

Step 4: 多樣性過濾
   ↓ 排除餘弦相似度 > 0.85 的重複範例

Step 5: 加入失敗反例
   ↓ 從 strategies_failed 撈 top-2 語意接近的反例

返回給 LLM：
  {
    "positive_examples": [3 個成功策略],
    "negative_examples": [2 個失敗反例]
  }
```

### 4.2 完整 SQL 實作

```sql
-- Step 1+2+3 一次完成（DuckDB 強項）
WITH 
-- Step 1: SQL 過濾
filtered AS (
    SELECT *
    FROM strategies_developed
    WHERE trading_session = ?         -- 來自 intent.trading_session
      AND logic_type = ?              -- 來自 intent.logic_type
      AND overfitting_flag = FALSE    -- 排除過擬合策略
),

-- Step 2: metadata 粗篩
metadata_top20 AS (
    SELECT id,
           array_cosine_similarity(metadata_vector, ?) AS meta_sim
    FROM filtered
    ORDER BY meta_sim DESC
    LIMIT 20
),

-- Step 3: semantic 精篩
semantic_top5 AS (
    SELECT m.id,
           array_cosine_similarity(s.semantic_vector, ?) AS sem_sim
    FROM metadata_top20 m
    JOIN strategies_developed s ON m.id = s.id
    ORDER BY sem_sim DESC
    LIMIT 5
)

SELECT s.*, t.sem_sim AS similarity
FROM semantic_top5 t
JOIN strategies_developed s ON t.id = s.id
ORDER BY t.sem_sim DESC;
```

### 4.3 Step 4：多樣性過濾（Python 實作）

```python
def diversity_filter(
    candidates: list[Strategy],
    similarity_threshold: float = 0.85,
    max_results: int = 3
) -> list[Strategy]:
    """
    排除過於相似的範例。
    若多個候選互相相似度 > 0.85，只保留 sharpe 最高的那個。
    """
    if not candidates:
        return []
    
    # 按 sharpe 排序（高到低）
    sorted_candidates = sorted(candidates, key=lambda s: s.sharpe, reverse=True)
    
    selected = []
    for candidate in sorted_candidates:
        # 檢查與已選的相似度
        is_diverse = True
        for already_selected in selected:
            sim = cosine_similarity(
                candidate.semantic_vector,
                already_selected.semantic_vector
            )
            if sim > similarity_threshold:
                is_diverse = False
                break
        
        if is_diverse:
            selected.append(candidate)
        
        if len(selected) >= max_results:
            break
    
    return selected
```

### 4.4 Step 5：失敗反例檢索

```python
def retrieve_failure_examples(
    intent_query_text: str,
    intent_vector: np.ndarray,
    k: int = 2
) -> list[FailureExample]:
    """
    從 strategies_failed 撈出語意接近的失敗反例。
    """
    sql = """
    SELECT *,
           array_cosine_similarity(semantic_vector, ?) AS sim
    FROM strategies_failed
    ORDER BY sim DESC
    LIMIT ?
    """
    rows = duckdb.execute(sql, [intent_vector, k]).fetchall()
    return [FailureExample.from_row(r) for r in rows]
```

### 4.5 完整檢索函式（彙整）

```python
def retrieve_for_generation(
    intent: Intent,
    embedder: Embedder
) -> RetrievalResult:
    """RAG 主函式：給 Step ② 用"""
    
    # 嵌入查詢文字
    query_text = f"{intent.trading_session} {intent.logic_type} {intent.user_prompt}"
    semantic_vec = embedder.encode(query_text)
    metadata_vec = embedder.encode(query_text[:200])
    
    # Step 1+2+3：DuckDB 一次完成
    candidates = duckdb_layered_search(
        trading_session=intent.trading_session,
        logic_type=intent.logic_type,
        metadata_vec=metadata_vec,
        semantic_vec=semantic_vec
    )
    
    # Step 4：多樣性過濾
    positive = diversity_filter(candidates, threshold=0.85, max_results=3)
    
    # 補足：若 positive < 3，從 library/ Few-shot 補
    if len(positive) < 3:
        positive += load_few_shot_fallback(
            trading_session=intent.trading_session,
            n_needed=3 - len(positive)
        )
    
    # Step 5：失敗反例
    negative = retrieve_failure_examples(query_text, semantic_vec, k=2)
    
    return RetrievalResult(
        positive_examples=positive,
        negative_examples=negative
    )
```

---

## 5. 失敗反例機制（核心品質機制 #3）

### 5.1 為什麼要把失敗也向量化

> 舊專案的 `memory/fail_patterns.md` 只是純文字記錄，**沒有向量化**，下次 LLM 生成時看不到反例，重複犯同樣的錯。

V1.4 的做法：

```
策略失敗 → 記錄
   ├─ what_was_tried（中文描述策略邏輯）
   ├─ why_failed（失敗原因）
   └─ failure_metrics（Sharpe = -2.45 等）
   ↓
嵌入向量化
   ↓
存入 strategies_failed Collection
   ↓
下次 RAG 檢索時，撈出 top-2 語意接近的失敗反例
   ↓
餵給 LLM 的 Step ③ prompt：「請避免犯下類似錯誤」
```

### 5.2 strategies_failed Schema

```sql
CREATE TABLE strategies_failed (
    id VARCHAR PRIMARY KEY,
    strategy_name VARCHAR NOT NULL,
    
    -- 反例核心內容
    what_was_tried TEXT NOT NULL,        -- 中文描述「曾經嘗試的策略邏輯」
    why_failed TEXT NOT NULL,            -- 失敗原因
    failure_metrics VARCHAR,              -- JSON: {"sharpe": -2.45, "max_dd": 0.62}
    
    -- 失敗階段（用於 Step ④/⑤ 的 prompt 反饋）
    failure_stage VARCHAR,                -- prompt_step / vectorbt / wfa / mc_compile
    failure_layer INTEGER,                -- 1, 2, 3 for EL validation
    
    -- 向量
    semantic_vector FLOAT[1024],
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_failed_semantic ON strategies_failed
    USING HNSW (semantic_vector) WITH (metric = 'cosine');
```

### 5.3 失敗策略的「what_was_tried」如何寫

當一個策略失敗時，自動產出中文描述（不是直接丟 EL 程式碼）：

```python
def summarize_failed_strategy(
    strategy: Strategy,
    llm: LLMClient
) -> str:
    """
    用 LLM 產出失敗策略的中文摘要（短，方便 RAG 找）。
    """
    prompt = f"""
    請用 100 字內中文摘要以下失敗策略的設計：
    
    Trading Session: {strategy.trading_session}
    Logic Type: {strategy.logic_type}
    
    YAML:
    {strategy.yaml_content}
    
    格式：純文字，描述其進場/出場/停損邏輯。
    """
    return llm.complete(prompt, max_tokens=300)
```

### 5.4 反例餵 Prompt 的方式

```python
# Step ③ 策略骨架的 prompt 中
prompt = f"""
... （前文略）

# 反面範例（請避免類似的設計缺陷）

{format_negative_examples(negative_examples)}

範例 1（失敗）:
- 嘗試: {what_was_tried_1}
- 失敗: {why_failed_1}（Sharpe -2.45）

範例 2（失敗）:
- 嘗試: {what_was_tried_2}
- 失敗: {why_failed_2}（過擬合：OOS/IS = 0.32）

請避免上述設計缺陷。
"""
```

---

## 6. 檢索品質評估（核心品質機制 #4）

### 6.1 為什麼要評估

「檢索準不準」直接影響 Few-shot 品質，進而影響 LLM 產出。**沒有持續評估就沒有持續改善**。

### 6.2 評估指標

#### 6.2.1 Recall@5

> 給定一個 query，正確答案是否在 top-5 結果中？

```python
def calculate_recall_at_k(
    test_queries: list[TestCase],
    k: int = 5
) -> float:
    """
    Recall@K = (top-K 中包含正確答案的 query 數) / 總 query 數
    """
    hits = 0
    for tc in test_queries:
        results = retrieve(tc.query, top_k=k)
        if any(r.id in tc.expected_ids for r in results):
            hits += 1
    return hits / len(test_queries)
```

#### 6.2.2 MRR (Mean Reciprocal Rank)

> 正確答案在第幾名？越前面分數越高。

```python
def calculate_mrr(test_queries: list[TestCase]) -> float:
    """
    MRR = mean(1 / rank_of_first_correct_result)
    """
    reciprocal_ranks = []
    for tc in test_queries:
        results = retrieve(tc.query, top_k=20)
        for rank, result in enumerate(results, start=1):
            if result.id in tc.expected_ids:
                reciprocal_ranks.append(1.0 / rank)
                break
        else:
            reciprocal_ranks.append(0.0)
    return sum(reciprocal_ranks) / len(reciprocal_ranks)
```

### 6.3 Test Set 建立

#### 6.3.1 Phase 1 結束時建立

```yaml
# tests/rag_test_set.yaml
test_cases:
  - id: tc_001
    query: "夜盤均值回歸策略，5 分 K"
    expected_ids: [strategy_A, strategy_B, strategy_C]
    rationale: "這幾個都是夜盤均值回歸"
  
  - id: tc_002
    query: "用布林通道做日盤當沖"
    expected_ids: [strategy_D]
    rationale: "TXDTA505 是經典範例"
  
  # ... 至少 30 個
```

#### 6.3.2 Phase 1 製作 Test Set 的工作流

1. Claude Code 從 `library/` 與舊專案 `library/` 中各策略的 description 反推「自然語言查詢」
2. 你（人類）審核每個 test case 的合理性（給 30 分鐘）
3. 寫進 `tests/rag_test_set.yaml`

### 6.4 自動化評估腳本

```python
# scripts/eval_rag.py
"""每個 Phase 結束時跑一次"""

def run_evaluation():
    test_cases = load_yaml("tests/rag_test_set.yaml")
    
    recall_5 = calculate_recall_at_k(test_cases, k=5)
    mrr = calculate_mrr(test_cases)
    
    # 寫進 quality_metrics 表
    db.insert("quality_metrics", {
        "metric_name": "rag_recall_at_5",
        "metric_value": recall_5,
        "phase": current_phase()
    })
    db.insert("quality_metrics", {
        "metric_name": "rag_mrr",
        "metric_value": mrr,
        "phase": current_phase()
    })
    
    # 產報告
    print(f"Recall@5: {recall_5:.2%}")
    print(f"MRR: {mrr:.3f}")
    
    # 退步警報
    last_recall = db.query("SELECT metric_value FROM quality_metrics "
                            "WHERE metric_name='rag_recall_at_5' "
                            "ORDER BY measured_at DESC LIMIT 2 OFFSET 1")
    if last_recall and recall_5 < last_recall - 0.05:
        warn(f"⚠️ Recall@5 退步了 {last_recall - recall_5:.2%}")
```

### 6.5 目標

| 指標 | Phase 1 目標 | Phase 5 目標 |
|---|---|---|
| Recall@5 | ≥ 70% | ≥ 85% |
| MRR | ≥ 0.45 | ≥ 0.65 |

---

## 7. 多樣性檢索（核心品質機制 #5）

### 7.1 為什麼

> 舊專案的另一個問題：**RAG 檢索沒有多樣性**，每次都撈出相似的策略，LLM 看到的 Few-shot 過於同質，產出策略也都長一樣。

### 7.2 兩層多樣性保護

#### 7.2.1 檢索時：排除過於相似的範例（已在 §4.3 實作）

只保留與已選範例 cosine_similarity ≤ 0.85 的候選。

#### 7.2.2 生成完成後：批次多樣性指標

每完成一批 50 個策略後，計算多樣性指標：

```python
def calculate_batch_diversity(strategies: list[Strategy]) -> dict:
    """
    計算一批策略的多樣性指標。
    """
    # 1. 兩兩相似度平均
    similarities = []
    for i, s1 in enumerate(strategies):
        for s2 in strategies[i+1:]:
            sim = cosine_similarity(s1.semantic_vector, s2.semantic_vector)
            similarities.append(sim)
    avg_pairwise_sim = sum(similarities) / len(similarities)
    
    # 2. trading_session 分佈
    session_counts = Counter(s.trading_session for s in strategies)
    
    # 3. logic_type 分佈
    logic_counts = Counter(s.logic_type for s in strategies)
    
    return {
        "avg_pairwise_similarity": avg_pairwise_sim,    # < 0.7 為健康
        "session_distribution": dict(session_counts),
        "logic_type_distribution": dict(logic_counts),
        "diversity_score": 1.0 - avg_pairwise_sim       # 越高越好
    }
```

### 7.3 多樣性不足時的自動處理

```python
def handle_low_diversity(metrics: dict, batch_id: str):
    """
    若多樣性 < 0.3，標記並提示使用者。
    """
    if metrics["diversity_score"] < 0.3:
        log.warning(
            f"批次 {batch_id} 多樣性低（score={metrics['diversity_score']:.2f}）"
        )
        # 寫進 quality_metrics
        db.insert("quality_metrics", {
            "metric_name": "low_diversity_alert",
            "metric_value": metrics["diversity_score"],
            "phase": current_phase()
        })
        # 下次自動觸發強制多樣化生成
        os.environ["FORCE_DIVERSIFY_NEXT_BATCH"] = "true"


def force_diversify_next_batch():
    """
    強制多樣化：下批生成時，RAG 檢索改為「優先撈不同 logic_type」。
    """
    # 修改 RAG 行為，從不同類別中各撈 1 個
    pass  # 實作細節
```

---

## 8. Collection 設計（V1.4 完整版）

### 8.1 4 個初始 Collection

| Collection | Schema 重點 | 啟用 Phase |
|---|---|---|
| `strategies_developed` | 三向量 + 結構化欄位 + 績效 | Phase 1 |
| `strategies_ideas` | 單向量（semantic）+ 來源 | Phase 1 |
| `strategies_failed` | 單向量（semantic）+ 失敗原因 | Phase 1 |
| `knowledge_web` | 單向量（semantic）+ URL 去重 | Phase 5 |

### 8.2 配置檔（YAML 驅動）

```yaml
# config/rag_collections.yaml
collections:
  - name: strategies_developed
    enabled: true
    weight: 1.0
    top_k: 5
    similarity_threshold: 0.7
    embed_model: bge-m3
    schema_type: three_vector              # 三向量 schema
    use_for_retrieval: true
  
  - name: strategies_ideas
    enabled: true
    weight: 0.6
    top_k: 3
    similarity_threshold: 0.65
    embed_model: bge-m3
    schema_type: single_vector
    use_for_retrieval: true
  
  - name: strategies_failed
    enabled: true
    weight: 0.4                            # 反例權重較低
    top_k: 2
    similarity_threshold: 0.7
    embed_model: bge-m3
    schema_type: single_vector
    use_for_retrieval: true
    role: negative_example                 # 標記為反例
  
  - name: knowledge_web
    enabled: false                         # Phase 5 才啟用
    weight: 0.5
    top_k: 5
    similarity_threshold: 0.65
    embed_model: bge-m3
    schema_type: single_vector
```

### 8.3 未來擴充路線圖

詳見 `docs/spec.md` §2.1.5。所有未來 Collection 都遵循 V1.4 命名空間規範：

- `strategies_*` / `knowledge_*` / `data_*` / `patterns_*` / `feedback_*` / `history_*`

---

## 9. 索引建立與重建

### 9.1 初始索引（Phase 1）

```bash
# 從 library/ 讀取所有策略 → 嵌入 → 入庫
python scripts/index_library.py --rebuild

# 建立 HNSW 索引（DuckDB 0.10+）
python scripts/build_indexes.py
```

### 9.2 增量更新

每次新策略通過 WFA 後，自動入庫：

```python
def index_new_strategy(strategy: Strategy):
    """新策略通過 WFA 後自動入庫"""
    vectors = generate_three_vectors(strategy)
    duckdb.execute("""
        INSERT INTO strategies_developed
        (id, name, trading_session, logic_type, ...,
         metadata_vector, semantic_vector, code_vector)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [...])
```

### 9.3 重建（Phase 結束時）

每個 Phase 結束跑一次完整重建，確保索引健康：

```bash
python scripts/index_library.py --rebuild
python scripts/eval_rag.py    # 跑 Recall@5 / MRR 評估
```

---

## 10. 與品質機制的對應

| 章節 | 對應品質機制 |
|---|---|
| §3 多向量分層 | #1 多向量分層嵌入 |
| §4 分層檢索 | #2 分層檢索策略 |
| §5 失敗反例 | #3 失敗反例向量化 |
| §6 檢索評估 | #4 檢索品質評估 |
| §7 多樣性 | #5 多樣性檢索（檢索時 + 批次後） |

---

## 11. 開發注意事項

### 11.1 嵌入模型固定不換（除非有重大理由）

- 一旦 Phase 1 用 bge-m3 開始嵌入，**全專案都用同一個模型**
- 換模型 = 全部重新嵌入（成本高）
- 若一定要換，必須 Phase Gate 評估

### 11.2 向量索引維護

- DuckDB HNSW 索引在大量 INSERT 後可能需要 REBUILD
- 每 Phase 結束跑一次 `OPTIMIZE` 或重建索引

### 11.3 不要直接寫 SQL，用封裝函式

```python
# 錯誤：直接寫 SQL
duckdb.execute("SELECT * FROM strategies_developed WHERE ...")

# 正確：用封裝函式
from src.core.ai_engine.vector_store import VectorStore
store = VectorStore()
results = store.search_developed(intent=..., top_k=5)
```

理由：未來擴充 Collection 或修改 schema 時，只需改一處。

---

**END OF rag_design.md**
