# AI 台指期策略生成系統 — 規格文件 V1.4

> **版本**：V1.4
> **建立日期**：2026-04-28
> **取代版本**：V1.3（2025-04-28）
> **適用對象**：開發者、Claude Code、專案協作人員

---

## 0. 專案背景與目的

### 0.1 背景
台指期策略開發傳統上依賴人工研究市場規律、手動撰寫 EasyLanguage 程式碼，並逐一在 MultiCharts 上執行回測，流程耗時且受限於個人視角的盲點。本專案以 AI 技術取代重複性高、耗時長的策略開發工作，大幅提升策略產出數量與品質。

### 0.2 專案目的
1. 以 AI 自動開發交易策略，減少人力投入與開發盲點
2. 以 AI 自動執行回測流程，提高篩選效率
3. 建立全自動化策略開發流水線
4. **本系統不負責實單交易**，僅做策略研發與 PLA 檔產出

### 0.3 V1.3 → V1.4 重大修訂

| 項目 | V1.3 / 早期討論 | V1.4 |
|---|---|---|
| 策略時段分類 | 含「全日當沖」 | 移除全日當沖，改為 4 類 |
| 滑價規則 | 未明確 | 統一 4 點 |
| chip 策略 | 列為可生成類型 | Phase 1 停用，Schema 預留 |
| 交易日定義 | 未明確 | 夜盤 15:00-05:00 併入隔日 |
| 強制平倉 | 口語描述 | 明確 13:40 / 04:45 K open |
| 冷卻期 | 未提及 | 收盤前 20 分鐘不開倉 |
| EL 驗證 | 未提及 | 三層驗證（規則 + LLM + MC） |
| 回測引擎 | 僅 Python | vectorbt 快篩 + backtrader WFA |
| 部署架構 | 跨機 WSL+Windows | 全 Windows 10 + Docker Desktop |
| 向量庫 | 早期討論 ChromaDB | DuckDB（沿用舊專案） |
| 嵌入模型 | 未指定 | BAAI/bge-m3（中文強、本地零成本） |
| LLM 編排 | 同時並行 | 儲備遞進（NIM → Minimax → Claude） |
| API 成本 | 未提及 | 每日 $10 USD + 三段式降級 |
| 品質保證 | 未提及 | 10 個品質機制（核心承諾） |
| Phase 規劃 | 未明確 | Phase 0-5 + 搬遷階段 |

---

## 1. 基礎說明

### 1.1 台灣期貨指數商品（TX）

- **交易時段**：
  - 日盤：08:45 - 13:45（週一 08:45 開始）
  - 夜盤：15:00 - 隔日 05:00（週六 05:00 結束）
- **每點價值**：新台幣 200 元
- **滑價假設**：每筆交易固定 **4 點（NTD 800）**

### 1.2 策略時段分類（4 類）

| 代碼 | 中文 | 觀察/開倉時段 | 冷卻期（不開倉） | 強制平倉 | 跨段 | 跨日 |
|---|---|---|---|---|---|---|
| `daytrade_day` | 日盤當沖 | 08:45-13:25 | **13:25-13:40** | **13:40 K open** | ❌ | ❌ |
| `daytrade_night` | 夜盤當沖 | 15:00-04:25 | **04:25-04:45** | **04:45 K open** | ❌ | ❌ |
| `swing_day` | 日盤波段 | 08:45-13:45 | 無 | 無 | ❌（不看夜盤）| ✅ |
| `swing_full` | 全日盤波段 | 0845-1345 + 1500-0500 | 無 | 無 | ✅ | ✅ |

**強制平倉設計理由**：用「下一根 K 棒 open」平倉，避免前視偏差（look-ahead bias）。

**冷卻期設計理由**：給已開倉部位有時間觸發停損/停利出場；避免「最後一根進場、下一根強平」的無效交易。

**移除**：~~全日當沖（V1.3 原有）~~

### 1.3 策略邏輯類型（6 類）

| 類型代碼 | 說明 | Phase 1 啟用 | 補資料後 |
|---|---|---|---|
| `trend` | 趨勢策略 | ✅ | ✅ |
| `mean_reversion` | 均值回歸 | ✅ | ✅ |
| `tunnel` | 通道突破 | ✅ | ✅ |
| `pattern` | K 線型態 | ✅ | ✅ |
| `chip` | 成交量與籌碼 | ❌ 停用 | ✅ |
| `reference` | 外部指標參考 | ⚠️ 條件開放 | ✅ |

### 1.4 交易日定義（重要）

**台指期交易日 T 包含**：
- 夜盤前段：T-1 自然日 15:00 ~ 23:59:59
- 夜盤後段：T 自然日 00:00 ~ 05:00
- 日盤：T 自然日 08:45 ~ 13:45

**實作要求**：
- 1 分 K 資料表必須有 `trading_day` 欄位
- ETL 入庫時以「資料本身判定」方法計算 trading_day（不依賴節假日表）
- WFA 切割以 `trading_day` 為單位

### 1.5 回測資料

- **格式**：CSV，欄位 `Date, Time, Open, High, Low, Close`
- **時區**：UTC+8（Asia/Taipei）
- **時間範圍**：2014-01-01 ~ 2025-12-31（**12 年完整資料**）
- **預估筆數**：約 130-260 萬筆 1 分 K
- **週期**：1 分 K（系統自動合成 5/15/30/60/日 K）
- **資料品質**：使用者已預處理，無需清洗
- **合約轉倉**：已是連續月資料，**ETL 不處理**轉倉
- **資料更新**：人工提供新檔案後手動觸發 ETL

### 1.6 開發加速：dev_data_range 機制

開發階段（Phase 1-2）為了加速回測，可透過 config 切換子集：

```yaml
# config/backtest.yaml
backtest_engine:
  full_data_range: "2014-01-01:2025-12-31"
  dev_data_range: "2022-01-01:2025-12-31"  # 4 年子集
  use_dev_range: true   # 開發時 true，生產時 false
```

---

## 2. 系統需求功能

### 2.1 向量知識庫（DuckDB + bge-m3）

#### 2.1.1 初始 Collections（Phase 1 啟用）

| Collection | 內容 | 對應 V1.3 |
|---|---|---|
| `strategies_developed` | 已通過驗證的成熟策略 + EL | `strategies` |
| `strategies_ideas` | 僅有想法的策略，尚無 EL | `strategy_ideas` |
| `strategies_failed` | 失敗策略反例（V1.4 新增）| — |
| `knowledge_web` | PTT/Mobile01 爬蟲內容 | `web_knowledge` |

#### 2.1.2 多向量分層嵌入（核心品質機制 #1）

每個策略產出 3 個向量分別存：
- `metadata_vector`：類別/時段/方向（200 字內）
- `semantic_vector`：description + notes（500 字內，純中文）
- `code_vector`：EL 程式碼（單獨嵌入）

**禁止做法**：把所有東西混成一段嵌入（這是舊專案問題的根源）

#### 2.1.3 分層檢索策略

```
Step 1: SQL 過濾結構化欄位（trading_session、logic_type）
   ↓
Step 2: metadata_vector 第一輪檢索（粗篩 top-20）
   ↓
Step 3: semantic_vector 第二輪檢索（精篩 top-5）
   ↓
Step 4: 多樣性過濾（餘弦相似度 > 0.85 視為重複，排除）
   ↓
Step 5: 返回 top-3 + 失敗反例 top-2 給 LLM
```

#### 2.1.4 可擴充架構（YAML 配置驅動）

```yaml
# config/rag_collections.yaml
collections:
  - name: strategies_developed
    enabled: true
    weight: 1.0
    top_k: 5
    similarity_threshold: 0.7
    embed_model: bge-m3
  - name: strategies_failed
    enabled: true
    weight: 0.4   # 反例權重較低
    top_k: 2
  # 未來可擴充
```

新增 Collection 流程：YAML 加 4-5 行 → 寫 ETL → **完全不用改程式碼**

#### 2.1.5 未來擴充路線圖（Phase 4+ Backlog）

| Collection | 用途 | Phase |
|---|---|---|
| `knowledge_news_macro` | 總經新聞 | 4+ |
| `knowledge_market_events` | 台股重大事件 | 4+ |
| `data_chip` | 法人籌碼 | 4+（資料補齊後） |
| `data_vix_regime` | VIX 區間標籤 | 4+ |
| `patterns_successful` | 通過 WFA 模式抽取 | 5+ |
| `feedback_user` | 使用者標記 | 5+ |
| `knowledge_mc_examples` | MC 官方 EL 範例 | 1+（隨時可加）|
| `history_optuna_params` | 參數調優歷史 | 3+ |

#### 2.1.6 爬蟲機制

- 黑白名單：`config/crawler_whitelist.yaml` / `crawler_blacklist.yaml`
- 去重：URL hash + 內容 hash 雙重比對
- 自動嵌入：爬取後立即向量化入 `knowledge_web`
- 排程：每日 / 每週可配置（APScheduler）
- Rate limit：每秒 1 個請求，避免被 ban

### 2.2 多 LLM 編排（儲備遞進）

#### 2.2.1 LLM 配置

| LLM | 用途 | 順序 | 備註 |
|---|---|---|---|
| **Nvidia NIM** | 第一順位（成本優先） | 1 | 免費額度 / 極低成本 |
| **Minimax M2** | 第二順位 | 2 | 中文強、便宜 |
| **Claude Sonnet 4.6** | 第三順位（高品質）| 3 | 失敗保險 |

**設計原則**：
- 同一個策略請求，**逐一嘗試**，1 個成功就停
- 各 LLM 失敗判定：EL 三層驗證任一層失敗即視為該 LLM 失敗
- LLM 設定以 YAML 集中管理（`config/llm_providers.yaml`）

#### 2.2.2 5 段 prompt 串接（核心品質機制 #2）

| Step | 任務 | LLM | 強制 JSON |
|---|---|---|---|
| ① | 意圖解讀 | 同一個 LLM（順位） | ✅ tool_use |
| ② | RAG 檢索 | 不用 LLM（DuckDB 查詢）| — |
| ③ | 策略骨架（YAML 偽代碼）| 同一個 LLM | ❌ |
| ④ | EL 程式碼生成 | 同一個 LLM | ❌ |
| ⑤ | 自我審核 | 同一個 LLM 換 prompt | ✅ tool_use |

**禁止做法**：把 5 段合併成一個大 prompt（這是舊專案的問題）

#### 2.2.3 預算控制（三段式降級）

```yaml
# config/budget.yaml
budget:
  daily_usd_limit: 10.0
  per_batch_size: 50          # 一批 50 個策略
  per_strategy_max_retry: 3
  
thresholds:
  normal_until: 7.0           # 0-70%：NIM → Minimax → Claude
  throttle_until: 9.5         # 70-95%：NIM → Minimax（Claude 停用）
  survival_until: 10.0        # 95-100%：全部 Minimax
  hard_stop_at: 10.0          # 拒絕新請求

reset_time: "00:00 Asia/Taipei"
```

#### 2.2.4 Prompt Cache（只 Claude）

Claude 部分的 system prompt + Few-shot 例子使用 Anthropic Prompt Cache，可省 40-60% 成本。NIM/Minimax 不接 cache（本來就便宜，實作成本不划算）。

### 2.3 EasyLanguage 三層驗證（核心品質機制 #3）

#### 第 1 層：規則式靜態檢查
- 必要關鍵字：`Inputs / Vars / If...Then`
- 配對檢查：`Begin/End`、`Buy/Sell` 對稱
- 必須包含停損
- 禁用語法黑名單（Phase 1：Volume / Ticks / OpenInt）
- **執行時間**：< 0.1 秒，**成本**：免費

#### 第 2 層：LLM 自我審核
- 同一 LLM、不同 prompt（審核者角色）
- 逐行檢查：語法信心、邏輯對齊、強平機制、禁用關鍵字
- 強制 JSON 輸出
- 信心 < 0.7 → 標記要修

#### 第 3 層：MC Bridge 試編譯
- 透過 COM 呼叫 PLEditor.exe 編譯
- 失敗訊息回灌給 LLM 自動修正（最多 3 次）
- 累積失敗模式進 `el_validation_log` 表 → 反餵 prompt

### 2.4 回測與篩選（雙引擎）

#### 2.4.1 vectorbt 快篩（向量化、快）

```yaml
python_filter:
  sharpe_min: 1.2
  max_drawdown_max: 0.35
  profit_factor_min: 1.3
  min_trades: 80
```

任一未達 → 直接淘汰，不進 WFA。

#### 2.4.2 backtrader WFA（事件驅動、精準）

- IS 18 個月 / OOS 6 個月 / 步進 6 個月
- 12 年資料 → 約 21 個窗口
- **合格條件**：所有 OOS Sharpe 均值 / IS Sharpe 均值 ≥ 0.6
- **未達**：標記 `overfitting_flag = True`，進人工複審佇列（不淘汰）

#### 2.4.3 並行化

- `multiprocessing.Pool`：每 process 跑 1 個策略
- 8 核機器 → 同時 4-6 個策略
- 50 個策略一批 → 約 1-2 小時（並行）

### 2.5 通過篩選的策略處理

#### 2.5.1 自動連結 MC → PLA 檔
- 透過 MC Bridge 服務（Windows 裸跑 FastAPI）
- COM 自動化操作 PLEditor.exe
- 寫 EL（cp950 編碼）→ Ctrl+F7 編譯 → 取得 PLA 檔

#### 2.5.2 策略說明報告（Markdown）

每個通過 WFA 的策略產出：
- **開發概要**（150 字內）：邏輯、進場、出場、停損
- **績效數據**：Sharpe / MaxDD / PF / 交易筆數 / 勝率 / 平均持倉時間 / 回測區間
- **風險提示**：市場環境依賴性、參數敏感度、IS/OOS 衰退程度
- **EL 程式碼全文**
- **WFA 21 個窗口表格**

### 2.6 Web UI 功能

完全沿用 V1.3 第 2.5 節定義（不擴充至月績效熱力圖等進階視覺化，列入 Issue #005）。

詳細規格見 `docs/api_contracts.md`。

---

## 3. 工作流程

### 3.1 向量資料庫維護
日常根據使用者輸入、爬蟲、新增策略更新（自動化排程）。

### 3.2 端到端執行流程

```
使用者在 Web UI 輸入 Prompt
         ↓
LLM 編排（5 段 prompt 串接）
   ① 意圖解讀（強制 JSON）
   ② RAG 檢索（DuckDB 多向量分層）
   ③ 策略骨架（YAML 偽代碼）
   ④ EL 程式碼生成
   ⑤ 自我審核（強制 JSON）
         ↓
EL 三層驗證
   第 1 層：規則檢查
   第 2 層：LLM 自審
   第 3 層：MC Bridge 試編譯
         ↓
vectorbt 快篩（KPI 門檻）
         ↓ 通過
backtrader WFA（21 窗口）
         ↓ 通過
產 PLA 檔 + 策略說明報告
         ↓
入 strategies_developed Collection（向量化）
```

每一步失敗 → 寫入 `failure_log`，不中斷整批，**累積反例餵下次生成**。

---

## 4. 最終交付物

- 向量資料庫（DuckDB）
- 可 import 至 MultiCharts 的 PLA 檔
- 策略說明報告（Markdown）
- Web UI 介面（含外網存取）
- Phase 0-5 工作說明書（每 Phase 結束產出）

---

## 5. 開發規範

### 5.1 規格凍結原則（V1.4 核心紀律）

**規格一旦定稿，Phase 進行中不接受功能新增。** 新需求一律寫進 `docs/issues_to_review.md`，等該 Phase 結束後評估是否進入下一 Phase。

### 5.2 Phase Gate 制度

每個 Phase 結束**必須通過驗收清單**才能進入下一 Phase。沒通過就停在原 Phase 補完，不准跳。

### 5.3 拒絕零碎原則

每個 commit 必須對應一個 Phase 內的 milestone。**不准做 Phase 範圍外的事**，即使「順手」也不行。

### 5.4 舊程式只能進不能改

舊專案的程式碼**只用「整段 copy」或「整段 rewrite」**，不准「東改一行西改一行」。避免污染新專案的清晰邊界。

### 5.5 程式碼規範

- Python 3.11+，Type Hints 全覆蓋
- `black` 格式化、`ruff` 靜態分析
- Google 風格 docstring
- 敏感資訊只從 `.env` 讀取

### 5.6 測試規範

- 每核心模組對應 unit test（pytest）
- LLM 呼叫用 fixture 替代，避免浪費 token
- 業務規則（時段分類、強平、滑價）必須有專屬測試 case

### 5.7 版本控制

- 分支：`main`（穩定）/ `develop`（開發中）/ `feature/phaseN-<topic>`
- Commit 格式：`[PhaseN] <type>: <subject>`
- 每 Phase 結束建立 Tag：`phase-N-complete`

### 5.8 錯誤處理

- Pipeline 任一步失敗，**只記錄並跳過該策略**，不中斷整批
- 所有外部呼叫（LLM API / MC COM / DB）包在 try-catch
- 失敗原因寫入 `failure_log`

### 5.9 每階段產出工作說明書（V1.3 5.5 條保留）

每 Phase 完成時產出 `docs/phase_reports/phase_N_report.md`，含：
1. Phase 目標回顧
2. 已完成模組清單
3. 重要設計決策與理由
4. 使用方式（給未來的 Claude Code 與人類看）
5. 已知限制與技術債
6. 下一 Phase 銜接建議

---

## 6. 品質保證機制（10 機制總覽）

詳細規格與實作見 `docs/quality_safeguards.md`。這裡僅列出總綱：

### 向量資料庫品質（5 項）
1. **多向量分層嵌入** — 元資料 / 語意 / 程式碼分離
2. **分層檢索策略** — SQL 過濾 → 粗篩 → 精篩 → 多樣性過濾
3. **失敗反例向量化** — `strategies_failed` 也參與 RAG
4. **檢索品質評估** — Recall@5 / MRR 指標每 Phase 評估
5. **多樣性檢索** — 排除餘弦相似度 > 0.85 的重複範例

### LLM 使用品質（5 項）
6. **5 段 prompt 串接** — 不允許退化為單一大 prompt
7. **儲備遞進 + 預算降級** — 三段式自動切換
8. **EL 三層驗證** — 規則 + LLM 自審 + MC 試編譯
9. **策略多樣性指標** — 每批生成自動計算多樣性，不足時觸發強制多樣化
10. **成本-品質追蹤** — 每呼叫記錄 `tokens / cost / downstream_outcome`，每週報告

---

## 7. Phase 計畫總覽

| Phase | 名稱 | 工時 | 期程 | 風險 |
|---|---|---|---|---|
| **0** | 對齊與重用清理 | 10h | 1 週 | 🟢 |
| **1** | 基礎設施與資料層 | 18h | 1.5 週 | 🟢 |
| **2** | 回測引擎與業務規則 | 30h | 2.5 週 | 🟡 |
| **3** | LLM 編排與 EL 生成 | 30h | 2.5 週 | 🟠 |
| **4** | Web UI 與外網存取 | 35h | 3 週 | 🟢 |
| **5** | 整合、爬蟲與自動化 | 22h | 2 週 | 🟢 |
| **搬遷** | 部署到桌機 | 13-21h | 3-5 天 | 🟡 |
| **總計** | | **158-166h** | **約 13 週** | |

詳細 Phase 規劃見 `docs/phases_plan.md`。

---

## 8. 待追蹤議題（Issue List）

詳見 `docs/issues_to_review.md`。摘要：

| # | 主題 | 觸發檢視 |
|---|---|---|
| 001 | LLM 儲備遞進順序調整 | Phase 1 結束 |
| 002 | 滑價是否改為跨日加倍 | Phase 4 後 |
| 003 | chip / reference 策略開放 | Phase 4+ |
| 004 | MC Bridge 啟動方式（NSSM）| Phase 2-3 後 |
| 005 | 進階視覺化擴充 | Phase 4+ |
| 006 | Web UI 外網方案決定 | Phase 4 |
| 007 | RAG 檢索品質追蹤 | 每 Phase 結束 |
| 008 | LLM 成本-品質比 | 每週 |
| 009 | 策略多樣性監控 | 每批生成 |
| 010 | 失敗模式收斂 | 每月 |

---

## 9. 法律與責任聲明

- 本系統為**策略研發工具**，不負責實單交易
- 所有產出策略**必須經人工審查**後才能用於實盤
- 回測績效不代表未來實盤結果
- 滑價、手續費等假設可能與實際成交有差異

---

## 10. 文件導航

| 文件 | 用途 | 語言 |
|---|---|---|
| `CLAUDE.md` | Claude Code 主控檔（每次必讀） | English |
| `docs/spec.md` | 本文件 | 中文 |
| `docs/business_rules.md` | 台指期業務規則細節 | 中文 |
| `docs/architecture.md` | 系統架構 + 三層重用 | 中文 |
| `docs/el_validation.md` | EL 三層驗證 | 中文 |
| `docs/llm_prompts.md` | 5 段 prompt + Few-shot | 中文 |
| `docs/rag_design.md` | 多向量檢索設計 | 中文 |
| `docs/api_contracts.md` | MC Bridge + Web API | 中文 |
| `docs/quality_safeguards.md` | 10 品質機制（每 Phase 必讀！）| 中文 |
| `docs/phases_plan.md` | Phase 0-5 詳規 | 中文 |
| `docs/issues_to_review.md` | Issue List | 中文 |
| `docs/phase_reports/` | 每 Phase 工作說明書 | 中文 |
| `docs/legacy/` | 舊專案參考材料 | 中文 |

---

**END OF V1.4 SPECIFICATION**
