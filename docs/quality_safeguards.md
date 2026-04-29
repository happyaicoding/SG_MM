# 品質保證機制（Quality Safeguards）

> **這是 V1.4 最重要的文件之一**。
> **每個 Phase 開始前，Claude Code 必讀本文件 §1 快檢清單**。
> **使用者每月手動 review 一次本文件 §2 詳細規格**。

---

## §1 快檢清單（每 Phase 開始前必讀）

### 1.1 Phase 入口檢查（5 分鐘可完成）

進入新 Phase 前，Claude Code 必須回答以下問題並寫進 Phase 報告：

```markdown
# Phase N 入口檢查（複製到 phase_N_report.md）

[ ] 我已讀完 docs/quality_safeguards.md §1
[ ] 本 Phase 涵蓋的品質機制：__________
[ ] 本 Phase 不該動的品質機制：__________
[ ] 本 Phase 結束前必須確認的品質指標：__________

## 我的承諾
我在本 Phase 內：
- 不會合併 5 段 prompt 為單一大 prompt
- 不會用混合嵌入取代多向量分層
- 不會跳過 EL 三層驗證的任一層
- 不會關閉預算控制機制
- 不會把失敗策略丟掉不存
- 每完成一個模組都會寫 unit test
```

### 1.2 各 Phase 涵蓋的品質機制速查

| Phase | 主要涵蓋的品質機制 | 不該動 |
|---|---|---|
| **Phase 0** | — | 全部 |
| **Phase 1** | #1 多向量、#3 失敗反例 schema、#4 RAG test set 建立 | #6/7/8 (Phase 3 才實作) |
| **Phase 2** | — | 全部（本 Phase 純回測引擎） |
| **Phase 3** | #6 5 段 prompt、#7 儲備遞進、#8 三層驗證、#10 成本追蹤 | #1/2/3/4/5（Phase 1 已實作） |
| **Phase 4** | #2 分層檢索 UI、#10 成本-品質儀表板 | 演算法層全部 |
| **Phase 5** | #5 多樣性指標、#9 多樣性監控 | 已穩定的全部 |

### 1.3 違反品質機制的紅旗訊號

如果 Claude Code 發現自己想做以下事，**必須停下來找使用者確認**：

- 🚨 「為了快速 demo，我先把 5 段 prompt 合成一段」 → **拒絕**
- 🚨 「失敗策略不重要，先不存」 → **拒絕**
- 🚨 「Layer 1 規則檢查覆蓋率夠了，先不寫 unit test」 → **拒絕**
- 🚨 「使用者很急，我先跳過 LLM 自我審核」 → **拒絕**
- 🚨 「多向量太複雜，先用單向量混合嵌入」 → **拒絕**
- 🚨 「預算控制等 Phase 5 再加」 → **拒絕**

### 1.4 Phase Gate 品質指標

每 Phase 結束前必須跑（自動化 script）：

```bash
# Phase 1 結束
python scripts/eval_rag.py            # Recall@5 ≥ 0.65（初始目標）

# Phase 2 結束
python scripts/eval_business_rules.py # 4 種 trading_session 各 1 個 case 通過

# Phase 3 結束
python scripts/eval_llm_pipeline.py   # 至少 5 個策略端到端通過
python scripts/eval_el_validation.py  # 三層驗證攔截率 ≥ 80%

# Phase 4 結束
# UI 手動驗收（業務驗收清單）

# Phase 5 結束
python scripts/eval_diversity.py      # diversity_score ≥ 0.4
python scripts/eval_cost_quality.py   # cost_per_passing_strategy 報告
```

---

## §2 10 個品質保證機制（詳細規格）

### 機制 #1 — 多向量分層嵌入

**問題**：舊專案把 metadata + description + EL 程式碼混在同一個向量，造成嵌入被「文字長度大的部分」主導。

**解法**：每個策略產出 3 個獨立向量：
- `metadata_vector`（200 字內）：類別、時段、方向、簡短概要
- `semantic_vector`(500 字內）：description、設計理念、備註
- `code_vector`：EL 程式碼

**詳細規格**：見 `docs/rag_design.md` §3。

**驗收**：
- DuckDB schema 含 3 個 FLOAT[1024] 欄位
- 各向量都有 HNSW 索引
- `generate_three_vectors()` 函式有 unit test

**Phase 啟用**：Phase 1（schema 建立）+ Phase 1（首批策略嵌入）

---

### 機制 #2 — 分層檢索策略

**問題**：舊專案只有單向量單次檢索，沒有 SQL 過濾、沒有分階段精篩。

**解法**：5 步驟檢索：
1. SQL 過濾結構化欄位
2. metadata_vector 粗篩（top-20）
3. semantic_vector 精篩（top-5）
4. 多樣性過濾（餘弦 > 0.85 排除）
5. 加入失敗反例（top-2）

**詳細規格**：見 `docs/rag_design.md` §4。

**驗收**：
- 用 30 個 test query 跑完整流程，Recall@5 ≥ 70%
- 多樣性過濾排除掉的數量寫進 log

**Phase 啟用**：Phase 1（基本實作）+ Phase 3（生產用）

---

### 機制 #3 — 失敗反例向量化

**問題**：舊專案 `memory/fail_patterns.md` 是純文字，不參與 RAG，LLM 重複犯同樣錯誤。

**解法**：
- `strategies_failed` Collection
- 每個失敗策略產出 `what_was_tried` + `why_failed` 中文摘要
- 嵌入後寫入 Collection
- 下次 RAG 撈 top-2 反例餵 prompt

**詳細規格**：見 `docs/rag_design.md` §5。

**驗收**：
- `strategies_failed` 表存在且有資料
- Step ③ 的 prompt 中可見 negative_examples 區塊

**Phase 啟用**：Phase 1（schema）+ Phase 3（自動化失敗 → 嵌入流程）

---

### 機制 #4 — 檢索品質評估

**問題**：舊專案沒有「檢索準不準」的測量機制，無法持續改善。

**解法**：
- 建立 30+ 個 test cases（Phase 1 結束時）
- 每 Phase 結束自動跑 Recall@5 / MRR
- 退步 > 5% 自動警告

**詳細規格**：見 `docs/rag_design.md` §6。

**驗收**：
- `tests/rag_test_set.yaml` 有 ≥ 30 個 cases
- `scripts/eval_rag.py` 可獨立執行
- `quality_metrics` 表有歷史紀錄

**指標目標**：

| Phase | Recall@5 | MRR |
|---|---|---|
| 1 | ≥ 60% | ≥ 0.40 |
| 3 | ≥ 75% | ≥ 0.50 |
| 5 | ≥ 85% | ≥ 0.65 |

**Phase 啟用**：Phase 1（test set + script）+ 每 Phase 結束評估

---

### 機制 #5 — 多樣性檢索

**問題**：舊專案每次 RAG 撈出來的策略都很像，LLM 看到的 Few-shot 同質，產出策略也重複。

**解法**：兩層多樣性保護：
1. **檢索時**：排除餘弦相似度 > 0.85 的範例
2. **生成完成後**：計算批次多樣性指標，不足時自動觸發強制多樣化

**詳細規格**：見 `docs/rag_design.md` §7。

**驗收**：
- `diversity_filter()` 函式有 unit test
- 每批生成後產出 diversity report

**指標目標**：
- 批次內 avg_pairwise_similarity < 0.7
- diversity_score（= 1 - 平均相似度）≥ 0.4

**Phase 啟用**：Phase 1（檢索時）+ Phase 5（批次監控）

---

### 機制 #6 — 5 段 prompt 串接

**問題**：舊專案用 132 行單一大 prompt，LLM 注意力分散，每件事都做不好。

**解法**：5 個獨立 prompt 串接：
- ① 意圖解讀（強制 JSON）
- ② RAG 檢索（不用 LLM）
- ③ 策略骨架（YAML 偽代碼）
- ④ EL 程式碼生成
- ⑤ 自我審核（強制 JSON）

**詳細規格**：見 `docs/llm_prompts.md`。

**禁止**：把任意兩段合併。即使「為了省 token」也不行（節省的 token 不會抵過品質下降）。

**驗收**：
- `src/core/ai_engine/prompt_steps/` 有 5 個獨立模組
- 每段失敗都有獨立 retry 機制
- `llm_calls` 表的 `prompt_step` 欄位記錄是哪一段

**Phase 啟用**：Phase 3

---

### 機制 #7 — 儲備遞進 + 預算降級

**問題**：舊專案是全域單一 LLM，沒有 fallback、沒有預算保護。

**解法**：
- **儲備遞進**：NIM → Minimax → Claude（成本優先）
- **三段式降級**：
  - normal (0-70%)：完整遞進
  - throttle (70-95%)：Claude 停用
  - survival (95-100%)：全部用 Minimax
- **硬停**：100% → 拒絕新請求

**詳細規格**：見 `docs/spec.md` §2.2.3。

**驗收**：
- `src/core/ai_engine/orchestrator.py` 含完整 fallback 邏輯
- `src/core/ai_engine/budget.py` 含三段式降級
- `budget_daily` 表記錄每日用量
- Web UI 顯示當前模式與用量

**Phase 啟用**：Phase 3

---

### 機制 #8 — EL 三層驗證

**問題**：舊專案只有第 1 層基本檢查，失敗策略卷進 MC 才發現問題。

**解法**：
- 第 1 層：規則式靜態檢查（< 0.1s，免費）
- 第 2 層：LLM 自我審核（同 LLM 不同 prompt）
- 第 3 層：MC Bridge 試編譯（最權威）
- 失敗回流：附錯誤訊息給 LLM 修正（最多 3 次）

**詳細規格**：見 `docs/el_validation.md`。

**驗收**：
- 三層各自有 unit test
- 端到端：故意產錯誤 EL，確認 retry 後修正
- `el_validation_log` 表有資料累積

**指標目標**：
- 第 1 層攔截率 ≥ 80%
- 第 3 層編譯通過率（包含 retry）≥ 70%

**Phase 啟用**：Phase 3

---

### 機制 #9 — 策略多樣性指標

**問題**：舊專案沒有「這批策略是否多樣」的測量，可能整批都是 MA 交叉。

**解法**：
- 每批生成完成後，自動計算：
  - 兩兩餘弦相似度平均
  - trading_session 分佈
  - logic_type 分佈
  - diversity_score
- 多樣性不足（< 0.3）→ 寫警告 + 下批強制多樣化

**詳細規格**：見 `docs/rag_design.md` §7.2.2。

**驗收**：
- `src/core/quality/diversity.py` 含完整指標計算
- 每批生成後自動寫進 `quality_metrics` 表

**Phase 啟用**：Phase 5（批次生成穩定後）

---

### 機制 #10 — 成本-品質追蹤

**問題**：舊專案沒有「花了多少錢、產出策略多優」的對照。

**解法**：每次 LLM 呼叫記錄：
- provider / model
- prompt_step（哪一段）
- tokens_in / tokens_out / cost_usd
- latency_ms
- success
- **downstream_outcome**：這次呼叫產出的策略最終是否通過 WFA

**每週自動產出報告**：
- 各 LLM 的「每元美金通過 WFA 的策略數」
- 各 prompt 步驟的失敗率
- 哪些查詢最容易失敗

**詳細規格**：

```sql
CREATE TABLE llm_calls (
    id INTEGER PRIMARY KEY,
    strategy_id INTEGER,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_step TEXT NOT NULL,
    tokens_in INTEGER,
    tokens_out INTEGER,
    cost_usd REAL,
    latency_ms INTEGER,
    success BOOLEAN,
    error_message TEXT,
    -- 後填欄位（策略完成 WFA 後寫入）
    downstream_strategy_passed BOOLEAN,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Phase 啟用**：Phase 3（基本記錄）+ Phase 5（每週報告自動化）

---

## §3 品質追蹤儀表板

### 3.1 即時儀表板（Web UI 顯示）

```
┌─────────────────────────────────────────────────┐
│  品質指標儀表板                                  │
├─────────────────────────────────────────────────┤
│  本日預算：$2.34 / $10.00 (23%)  [normal mode]  │
│  本週累積：$8.50                                 │
├─────────────────────────────────────────────────┤
│  策略總數：234   本週新增：12   平均 Sharpe：1.45│
│  待審核：8       過擬合：23                       │
├─────────────────────────────────────────────────┤
│  RAG Recall@5：0.78（上次評估 2 週前）            │
│  最近批次多樣性：0.45（健康）                     │
│  EL Layer 1 攔截率：92%                          │
│  EL Layer 3 編譯通過率：65%                       │
├─────────────────────────────────────────────────┤
│  LLM 通過 WFA 成功率（每元美金產出策略數）        │
│  - NIM:     0.32 個 / $1                        │
│  - Minimax: 0.55 個 / $1                        │
│  - Claude:  0.85 個 / $1                        │
└─────────────────────────────────────────────────┘
```

### 3.2 每週報告（自動產出）

```
docs/quality_reports/2026_W18_report.md

# 品質週報 2026-W18 (Apr 28 - May 4)

## 摘要
- 本週生成 87 個策略，通過 WFA 12 個
- 整體 cost-quality ratio 下降 5%（警告）

## 詳細指標
（自動產出表格）

## 異常警告
- ⚠️ NIM 成功率連續 2 週低於 30%（觸發 Issue #001 評估）
- ⚠️ Recall@5 從 0.82 跌到 0.78

## 下週優先事項
1. 評估 NIM 是否該降為第二順位
2. 檢視 RAG test set 是否需更新
```

---

## §4 違反品質機制的後果

### 4.1 Phase 內違反

如果 Claude Code 在 Phase 中違反任一品質機制：

```
Phase Gate 不通過
    ↓
不能進入下一 Phase
    ↓
必須在原 Phase 補完並修正
```

### 4.2 跨 Phase 違反（更嚴重）

如果 Phase X 沒實作的機制，Phase X+1 想用：

```
不允許「事後補做」
    ↓
寫進 Issue List 列為技術債
    ↓
Phase 5 結束前必須清償
```

### 4.3 規格凍結原則

> 一旦 V1.4 鎖定，10 個品質機制**不接受刪減**。
>
> 例外：機制本身被證明是錯的（需 Issue + 提案 + 評估）。
> 「太忙」「太複雜」「使用者很急」都不是有效理由。

---

## §5 給 Claude Code 的明確指令

當 Claude Code 進入新 Phase 時，第一件事：

```
1. cd <project_root>
2. cat docs/quality_safeguards.md  → 讀完 §1
3. 在 docs/phase_reports/phase_N_report.md 寫入 §1.1 入口檢查
4. git commit -m "[PhaseN] chore: phase entry quality check"
5. 才能開始第一個任務
```

每個 Phase 結束時：

```
1. 跑 Phase Gate 品質指標 script
2. 把結果寫進 phase_N_report.md
3. 更新 quality_metrics 表
4. CLAUDE.md 的 Quality Metrics 區塊更新
5. 確認 Phase Gate 通過後才能進下一 Phase
```

---

## §6 與其他文件的關聯

| 機制 | 主要實作文件 |
|---|---|
| #1 多向量分層 | `docs/rag_design.md` §3 |
| #2 分層檢索 | `docs/rag_design.md` §4 |
| #3 失敗反例 | `docs/rag_design.md` §5 |
| #4 檢索評估 | `docs/rag_design.md` §6 |
| #5 多樣性 | `docs/rag_design.md` §7 |
| #6 5 段 prompt | `docs/llm_prompts.md` 全文 |
| #7 儲備遞進 + 預算 | `docs/spec.md` §2.2 |
| #8 EL 三層驗證 | `docs/el_validation.md` 全文 |
| #9 多樣性指標 | `docs/rag_design.md` §7.2.2 |
| #10 成本-品質 | `docs/architecture.md` §5.1 |

---

**END OF quality_safeguards.md**
