# 系統架構文件

> **文件用途**：系統元件、資料流、目錄結構、三層重用清單
> **語言**：中文
> **適用對象**：開發者、Claude Code 進入新模組前必讀

---

## 1. 全局架構圖

### 1.1 部署架構（單機開發 + 一次性搬遷）

```
[ 開發階段（Phase 0-5）]              [ 部署階段（搬遷後）]
       筆電 (Windows 10)                   桌機 (Windows 10, 24/7)
   ┌─────────────────────────┐         ┌─────────────────────────┐
   │ Git 工作目錄              │  ──→   │ Git 工作目錄              │
   │ Docker Desktop           │  ──→   │ Docker Desktop           │
   │   ├─ app 容器            │         │   ├─ app 容器            │
   │   └─ webui 容器          │         │   └─ webui 容器          │
   │ uv venv (本機)           │  ──→   │ uv venv (本機)           │
   │   └─ MC Bridge (裸跑)    │         │   └─ MC Bridge (裸跑)    │
   │ MultiCharts (測試)       │  ──→   │ MultiCharts (上線)       │
   │ data/ (本機)             │  ──→   │ data/ (本機)             │
   └─────────────────────────┘         └─────────────────────────┘
                                              │
                                              ↓
                                        Cloudflare Tunnel
                                        / Port Forward (Issue #006)
                                              ↓
                                        外網（手機、太太公司）
```

### 1.2 服務分配（單機）

```
Windows 10 主機
│
├─ 【裸跑層】Python venv（必須，因為 COM 限制）
│   └─ MC Bridge Service (FastAPI :8001)
│       ├─ pywin32 + pywinauto
│       ├─ COM 操作 PLEditor.exe
│       └─ Stateless 微服務
│
└─ 【Docker 層】docker-compose
    │
    ├─ app 容器（主應用）
    │   ├─ FastAPI 主後端 :8000
    │   ├─ LLM 編排器
    │   ├─ 雙引擎回測（vectorbt + backtrader）
    │   ├─ 任務排程（APScheduler）
    │   ├─ 嵌入器（bge-m3）
    │   ├─ DuckDB（向量庫）
    │   ├─ SQLite（主資料庫）
    │   └─ 透過 host.docker.internal:8001 呼叫 MC Bridge
    │
    └─ webui 容器
        ├─ Nginx
        ├─ React 靜態檔
        └─ proxy /api/* → app:8000
```

---

## 2. 完整目錄結構

```
aismart/
├── CLAUDE.md                          # 主控檔（英文，≤200 行）
├── docker-compose.yml
├── pyproject.toml                     # uv 管理
├── uv.lock
├── .env.example
├── .gitignore
├── README.md                          # 對外說明（簡短）
│
├── docs/                              # 中文文件
│   ├── spec.md                        # V1.4 主規格
│   ├── business_rules.md              # 台指期業務規則
│   ├── architecture.md                # 本文件
│   ├── el_validation.md               # EL 三層驗證
│   ├── llm_prompts.md                 # 5 段 prompt + Few-shot
│   ├── rag_design.md                  # 多向量檢索設計
│   ├── api_contracts.md               # MC Bridge + Web API
│   ├── quality_safeguards.md          # 10 品質機制（每 Phase 必讀）
│   ├── phases_plan.md                 # Phase 0-5 + 搬遷
│   ├── issues_to_review.md            # Issue 追蹤
│   ├── deployment_guide.md            # 搬遷部署手冊（Phase 5 後寫）
│   ├── changelog.md                   # 自動產生
│   ├── legacy_assets_inventory.md     # 舊專案資產清單
│   ├── phase_reports/                 # 每 Phase 工作說明書
│   │   ├── phase_0_report.md
│   │   ├── phase_1_report.md
│   │   └── ...
│   └── legacy/                        # 舊專案參考（read-only）
│       ├── README.md
│       ├── reusable_snippets.md
│       └── original_source/           # 從舊專案 copy 的關鍵檔
│
├── src/
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py                  # 配置載入器
│   │   ├── db.py                      # SQLite + DuckDB 連線管理
│   │   ├── logging_config.py
│   │   │
│   │   ├── data/
│   │   │   ├── etl.py                 # CSV ETL + trading_day
│   │   │   ├── trading_day.py         # 交易日邏輯
│   │   │   └── data_view.py           # dev_data_range 切換
│   │   │
│   │   ├── ai_engine/
│   │   │   ├── __init__.py
│   │   │   ├── client.py              # LLM client（沿用舊專案）
│   │   │   ├── orchestrator.py        # 儲備遞進編排（V1.4 新增）
│   │   │   ├── budget.py              # 三段式預算控制（V1.4 新增）
│   │   │   ├── embedder.py            # bge-m3 嵌入器
│   │   │   ├── library.py             # Few-shot 策略庫
│   │   │   ├── vector_store.py        # DuckDB 向量庫
│   │   │   ├── rag.py                 # 多向量分層檢索（V1.4 新增）
│   │   │   ├── prompt_steps/          # 5 段 prompt 模組
│   │   │   │   ├── step1_intent.py
│   │   │   │   ├── step2_rag.py
│   │   │   │   ├── step3_skeleton.py
│   │   │   │   ├── step4_el.py
│   │   │   │   └── step5_critique.py
│   │   │   └── prompt_templates/      # Markdown 模板
│   │   │       ├── intent_parser.md
│   │   │       ├── strategy_skeleton.md
│   │   │       ├── el_generator.md
│   │   │       └── self_critique.md
│   │   │
│   │   ├── el_validation/             # EL 三層驗證（V1.4 新增）
│   │   │   ├── __init__.py
│   │   │   ├── layer1_static.py       # 規則式靜態檢查
│   │   │   ├── layer2_llm.py          # LLM 自我審核
│   │   │   ├── layer3_compile.py      # MC Bridge 試編譯
│   │   │   └── failure_log.py         # 失敗模式累積
│   │   │
│   │   ├── backtest/
│   │   │   ├── __init__.py
│   │   │   ├── business_rules.py      # 時段、強平、冷卻期、滑價
│   │   │   ├── vectorbt_filter.py     # 快篩
│   │   │   ├── backtrader_wfa.py      # WFA
│   │   │   ├── kpi.py                 # 6 指標計算
│   │   │   ├── parallel.py            # 並行化
│   │   │   └── strategy_base.py       # backtrader 業務規則基類
│   │   │
│   │   ├── mc_bridge/                 # MC COM 模組
│   │   │   ├── __init__.py
│   │   │   ├── server.py              # FastAPI :8001
│   │   │   ├── compiler.py            # PLEditor Ctrl+F7（沿用舊）
│   │   │   ├── el_writer.py           # cp950 寫入（沿用舊）
│   │   │   ├── dialog_guard.py        # 對話框守衛（沿用舊）
│   │   │   ├── pla_extractor.py       # PLA 檔產出
│   │   │   └── spr_parser.py          # Strategy Performance Report 解析
│   │   │
│   │   ├── crawler/                   # Phase 5
│   │   │   ├── __init__.py
│   │   │   ├── ptt.py
│   │   │   ├── mobile01.py
│   │   │   ├── dedup.py               # URL hash + 內容 hash
│   │   │   └── pipeline.py            # 自動向量化入庫
│   │   │
│   │   ├── reporting/
│   │   │   ├── __init__.py
│   │   │   └── strategy_report.py     # Markdown 策略報告
│   │   │
│   │   └── quality/                   # 品質機制（V1.4 新增）
│   │       ├── __init__.py
│   │       ├── diversity.py           # 策略多樣性指標
│   │       ├── cost_quality.py        # 成本-品質追蹤
│   │       └── rag_evaluator.py       # Recall@5, MRR
│   │
│   └── web/
│       ├── __init__.py
│       ├── api/
│       │   ├── strategies.py
│       │   ├── backtest.py
│       │   ├── kpi.py
│       │   ├── budget.py
│       │   └── settings.py
│       └── main.py                    # FastAPI app :8000
│
├── webui/                             # React 前端（Phase 4）
│   ├── package.json
│   ├── vite.config.ts
│   ├── src/
│   │   ├── App.tsx
│   │   ├── pages/
│   │   ├── components/
│   │   └── lib/
│   └── Dockerfile
│
├── tests/                             # pytest
│   ├── conftest.py
│   ├── unit/
│   ├── integration/
│   └── fixtures/
│
├── scripts/
│   ├── verify_mc_connection.py        # 沿用舊專案
│   ├── index_library.py               # 沿用舊專案
│   ├── setup_legacy_assets.py         # Phase 0 用
│   ├── migrate_db.py
│   └── setup_few_shot.py              # Phase 3 用
│
├── library/                           # Few-shot 策略庫
│   ├── README.md
│   ├── Daytrade/
│   │   ├── TXDTA505.ELS               # 沿用舊專案
│   │   ├── TXDTA505.yaml
│   │   └── ...                        # Phase 3 補完 5-10 個
│   └── swing/
│
├── config/
│   ├── budget.yaml
│   ├── llm_providers.yaml
│   ├── rag_collections.yaml
│   ├── crawler_whitelist.yaml
│   ├── crawler_blacklist.yaml
│   ├── backtest.yaml
│   └── schedules.yaml
│
└── data/                              # 持久化資料（git ignore）
    ├── csv/                           # 原始 1 分 K
    ├── sqlite/main.db
    ├── duckdb/strategy_vectors.duckdb
    ├── strategies/                    # 產出 YAML
    ├── pla_files/                     # 產出 PLA
    ├── reports/                       # SPR + 策略報告
    ├── logs/
    └── models/                        # bge-m3 模型快取
```

---

## 3. 三層重用清單（從舊專案）

> **核心原則**：舊程式只能進不能改。整段 copy 或整段 rewrite，不准片段修改。

### 3.1 Layer 1：直接重用（整段 copy）

> 這些模組已是生產級品質，**Phase 0 直接從舊專案 copy 到新 repo**，不修改。

| 來源 | 目標 | 行數 | 重用理由 |
|---|---|---|---|
| `legacy/src/core/backtest/mc_bridge.py` | `src/core/mc_bridge/compiler.py` 等 | 1261 | 1261 行生產級 MC COM，含對話框守衛、cp950 處理、SPR 解析 |
| `legacy/src/core/ai_engine/client.py` | `src/core/ai_engine/client.py` | 463 | Protocol 介面 + 工廠函式，多 LLM 支援已實作 |
| `legacy/src/core/ai_engine/vector_store.py` | `src/core/ai_engine/vector_store.py` | 222 | DuckDB 向量庫，已實作 upsert/query |
| `legacy/src/core/backtest/python_bt.py` | `src/core/backtest/vectorbt_filter.py` | 146 | vectorbt 整合 |
| `legacy/scripts/verify_mc_connection.py` | `scripts/verify_mc_connection.py` | 已看過 | 9 步驟 MC 連通性診斷 |
| `legacy/scripts/index_library.py` | `scripts/index_library.py` | 已看過 | 向量索引重建腳本 |
| `legacy/library/Daytrade/TXDTA505.*` | `library/Daytrade/TXDTA505.*` | — | 真實實戰策略，Few-shot #1 |
| `legacy/src/core/ai_engine/embedder.py` | `src/core/ai_engine/embedder.py` | — | bge-m3 嵌入器 |

**Layer 1 總量**：約 2500 行可直接 copy

### 3.2 Layer 2：重構對齊（保留設計 + 改寫部分）

> 這些模組設計良好，但**命名或結構需與 V1.4 對齊**。

| 來源 | 改寫重點 |
|---|---|
| `legacy/src/core/ai_engine/library.py` | `holding_type` → `trading_session`，加入 4 種時段 |
| `legacy/src/core/backtest/wfa.py` | 門檻參數化（YAML 配置驅動），對齊 18/6/6 月 |
| `legacy/config.yaml` | 加入：4 種時段、冷卻期、預算三段式、儲備遞進、多向量配置 |
| `legacy/CLAUDE.md` | 改寫為英文、加 Glossary、對齊 V1.4 32 決策 |
| `legacy/src/core/ai_engine/prompt_templates/*.md` | 改為 5 段獨立 prompt（不可合併大 prompt） |
| `legacy/main.py` | 加入新指令：`generate / orchestrate / budget` |

**Layer 2 總量**：約 1500 行需重構

### 3.3 Layer 3：全新開發（V1.4 新增）

> 這些是舊專案沒有的，**Phase 1-5 從零開發**。

| 模組 | 對應 Phase | 說明 |
|---|---|---|
| `src/core/ai_engine/orchestrator.py` | Phase 3 | 儲備遞進編排（NIM → Minimax → Claude） |
| `src/core/ai_engine/budget.py` | Phase 3 | 三段式預算降級 |
| `src/core/ai_engine/rag.py` | Phase 1+3 | 多向量分層檢索 |
| `src/core/el_validation/layer1_static.py` | Phase 3 | 規則式 EL 檢查 |
| `src/core/el_validation/layer2_llm.py` | Phase 3 | LLM 自我審核 |
| `src/core/el_validation/layer3_compile.py` | Phase 3 | MC 試編譯整合 |
| `src/core/backtest/business_rules.py` | Phase 2 | 時段、冷卻期、強平、滑價 |
| `src/core/backtest/backtrader_wfa.py` | Phase 2 | WFA 並行化 |
| `src/core/quality/*` | Phase 1+ | 4 個品質追蹤模組 |
| `src/core/crawler/*` | Phase 5 | PTT/Mobile01 爬蟲 |
| `webui/` | Phase 4 | React Web UI |
| `src/core/data/trading_day.py` | Phase 1 | 「資料本身判定」trading_day |

**Layer 3 總量**：約 2000 行新代碼

### 3.4 Phase 0 重用流程

```bash
# scripts/setup_legacy_assets.py 自動化執行
# 1. 從 docs/legacy/original_source/ 抽取 Layer 1 程式碼
# 2. 按 V1.4 目錄結構放置
# 3. 自動寫好 import 路徑
# 4. 跑 ruff / black 確認格式
# 5. 不啟動功能（只確保 import 不爆）
```

---

## 4. 資料流

### 4.1 端到端策略生成流程

```
[使用者] 在 Web UI 輸入 prompt
    ↓
[FastAPI :8000] /api/strategies/generate/stream
    ↓
[LLM Orchestrator]
    ├─ 預算守門員 (budget.py)：判斷當前模式（normal/throttle/survival）
    ├─ 選 LLM（NIM → Minimax → Claude）
    ├─ Step ① 意圖解讀（強制 JSON）
    ├─ Step ② RAG 檢索 (rag.py)
    │     ├─ SQL 過濾結構化欄位
    │     ├─ metadata_vector 粗篩（top-20）
    │     ├─ semantic_vector 精篩（top-5）
    │     ├─ 多樣性過濾（排除餘弦 > 0.85）
    │     └─ 撈 strategies_failed top-2 當反例
    ├─ Step ③ 策略骨架（YAML 偽代碼）
    ├─ Step ④ EL 程式碼生成
    └─ Step ⑤ 自我審核（強制 JSON）
    ↓
[EL 三層驗證]
    ├─ Layer 1 規則檢查 (< 0.1s)
    ├─ Layer 2 LLM 自審
    └─ Layer 3 MC Bridge 試編譯
        └─ HTTP POST → mc_bridge:8001/api/compile
            ├─ 寫 EL 到 Studies 目錄（cp950）
            ├─ PLEditor Ctrl+F7
            └─ 回傳：success / compile_errors
    ↓
[vectorbt 快篩] (向量化，1 分鐘內)
    └─ Sharpe / MaxDD / PF / Trades 門檻
    ↓
[backtrader WFA] (並行，1-2 小時/批)
    └─ 21 窗口 × IS 18m / OOS 6m
    ↓
[寫入結果]
    ├─ SQLite: backtest_results / wfa_windows / wfa_summary / llm_calls
    ├─ DuckDB: strategies_developed Collection（含 3 個向量）
    ├─ data/pla_files/: PLA 檔
    └─ data/reports/: 策略 Markdown 報告
    ↓
[品質追蹤]
    ├─ 多樣性指標計算
    ├─ 成本-品質比寫入
    └─ 失敗策略寫入 strategies_failed
```

### 4.2 失敗回流

```
任一步失敗
    ↓
寫入 failure_log（含階段、原因、details）
    ↓
若是 EL 三層驗證失敗 → 失敗訊息附在 prompt 重生（最多 3 次）
若是 LLM 失敗（超時/格式錯誤）→ 換下一個 LLM
若是預算耗盡 → 切換 throttle/survival 模式
    ↓
不中斷整批，繼續處理下一個策略
```

---

## 5. 資料庫 Schema

### 5.1 SQLite（主資料庫）

```sql
-- 資料層
CREATE TABLE minute_kbar (...);             -- 詳見 business_rules.md §2.4
CREATE TABLE data_meta (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TIMESTAMP
);

-- 策略層
CREATE TABLE strategies (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    trading_session TEXT NOT NULL,          -- daytrade_day, etc
    logic_type TEXT NOT NULL,               -- trend, mean_reversion, etc
    timeframe TEXT NOT NULL,                -- 1min, 5min, etc
    status TEXT NOT NULL,                   -- pending, validating, passed, failed
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE strategy_yaml (
    strategy_id INTEGER PRIMARY KEY,
    yaml_content TEXT NOT NULL,
    FOREIGN KEY (strategy_id) REFERENCES strategies(id)
);

CREATE TABLE strategy_el_code (
    strategy_id INTEGER PRIMARY KEY,
    el_content TEXT NOT NULL,
    el_version INTEGER DEFAULT 1,
    pla_file_path TEXT,
    FOREIGN KEY (strategy_id) REFERENCES strategies(id)
);

-- 回測層
CREATE TABLE backtest_results (
    id INTEGER PRIMARY KEY,
    strategy_id INTEGER NOT NULL,
    engine TEXT NOT NULL,                    -- vectorbt | backtrader
    sharpe REAL, max_drawdown REAL, profit_factor REAL,
    total_trades INTEGER, win_rate REAL,
    avg_holding_minutes REAL,
    data_range_start DATE, data_range_end DATE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (strategy_id) REFERENCES strategies(id)
);

CREATE TABLE wfa_windows (
    id INTEGER PRIMARY KEY,
    strategy_id INTEGER NOT NULL,
    window_index INTEGER NOT NULL,
    is_start DATE, is_end DATE,
    oos_start DATE, oos_end DATE,
    is_sharpe REAL, oos_sharpe REAL,
    FOREIGN KEY (strategy_id) REFERENCES strategies(id)
);

CREATE TABLE wfa_summary (
    strategy_id INTEGER PRIMARY KEY,
    avg_is_sharpe REAL,
    avg_oos_sharpe REAL,
    oos_is_ratio REAL,
    overfitting_flag BOOLEAN DEFAULT FALSE,
    FOREIGN KEY (strategy_id) REFERENCES strategies(id)
);

-- LLM / EL 層
CREATE TABLE llm_calls (
    id INTEGER PRIMARY KEY,
    strategy_id INTEGER,
    provider TEXT NOT NULL,                  -- nim | minimax | claude
    model TEXT NOT NULL,
    prompt_step TEXT NOT NULL,               -- step1 .. step5
    tokens_in INTEGER, tokens_out INTEGER,
    cost_usd REAL,
    latency_ms INTEGER,
    success BOOLEAN,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE el_validation_log (
    id INTEGER PRIMARY KEY,
    strategy_id INTEGER NOT NULL,
    layer INTEGER NOT NULL,                  -- 1 | 2 | 3
    success BOOLEAN NOT NULL,
    error_pattern TEXT,                      -- 累積反餵 prompt 用
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE budget_daily (
    date DATE PRIMARY KEY,
    total_cost_usd REAL DEFAULT 0,
    nim_cost_usd REAL DEFAULT 0,
    minimax_cost_usd REAL DEFAULT 0,
    claude_cost_usd REAL DEFAULT 0,
    mode TEXT DEFAULT 'normal'               -- normal | throttle | survival | hard_stop
);

-- 失敗追蹤
CREATE TABLE failure_log (
    id INTEGER PRIMARY KEY,
    strategy_name TEXT NOT NULL,
    failure_reason TEXT NOT NULL,
    failure_stage TEXT NOT NULL,
    details TEXT,                            -- JSON
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 品質指標
CREATE TABLE quality_metrics (
    id INTEGER PRIMARY KEY,
    metric_name TEXT NOT NULL,               -- diversity_index | recall_at_5 | etc
    metric_value REAL NOT NULL,
    phase TEXT,
    measured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 5.2 DuckDB（向量庫）

```sql
-- strategies_developed Collection
CREATE TABLE strategies_developed (
    id VARCHAR PRIMARY KEY,
    name VARCHAR NOT NULL,
    trading_session VARCHAR NOT NULL,
    logic_type VARCHAR NOT NULL,
    timeframe VARCHAR NOT NULL,
    direction VARCHAR,                       -- long | short | both
    -- 三向量分層（V1.4 核心）
    metadata_vector FLOAT[1024],
    semantic_vector FLOAT[1024],
    code_vector FLOAT[1024],
    -- 內容
    description TEXT,
    notes TEXT,
    el_code TEXT,
    yaml_content TEXT,
    -- 績效
    sharpe REAL, max_drawdown REAL, profit_factor REAL,
    overfitting_flag BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 向量索引（HNSW）
CREATE INDEX idx_dev_metadata ON strategies_developed
    USING HNSW (metadata_vector) WITH (metric = 'cosine');
CREATE INDEX idx_dev_semantic ON strategies_developed
    USING HNSW (semantic_vector) WITH (metric = 'cosine');
CREATE INDEX idx_dev_code ON strategies_developed
    USING HNSW (code_vector) WITH (metric = 'cosine');

-- strategies_ideas Collection
CREATE TABLE strategies_ideas (
    id VARCHAR PRIMARY KEY,
    title VARCHAR NOT NULL,
    semantic_vector FLOAT[1024],
    description TEXT,
    source TEXT,                             -- user_input | crawled | manual
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- strategies_failed Collection（V1.4 新增）
CREATE TABLE strategies_failed (
    id VARCHAR PRIMARY KEY,
    what_was_tried TEXT NOT NULL,            -- 中文描述策略邏輯
    why_failed TEXT NOT NULL,                -- 失敗原因
    failure_metrics VARCHAR,                  -- JSON: sharpe, max_dd, etc
    semantic_vector FLOAT[1024],
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- knowledge_web Collection
CREATE TABLE knowledge_web (
    id VARCHAR PRIMARY KEY,
    url VARCHAR UNIQUE NOT NULL,
    title TEXT,
    content TEXT,
    semantic_vector FLOAT[1024],
    source TEXT,                             -- ptt | mobile01
    crawled_at TIMESTAMP
);
```

---

## 6. 關鍵設計決策說明

### 6.1 為什麼 SQLite + DuckDB 並存

- **SQLite**：交易型操作（INSERT、UPDATE 頻繁），主資料庫
- **DuckDB**：分析型操作（SELECT 大量、向量檢索），向量庫
- 兩者**同進程**使用，不互相干擾
- 都是單檔，部署複雜度極低

### 6.2 為什麼 MC Bridge 不能容器化

- COM 物件必須在 Windows 主機 process 上
- Docker Linux 容器無 Windows API
- Docker Windows 容器內 MC 安裝困難 + 授權限制
- → 唯一可行：Windows 裸跑

### 6.3 為什麼 BAAI/bge-m3

- 中文表現顯著優於 OpenAI（PTT/Mobile01 主要是中文）
- 完全本地、零成本、零網路延遲
- 一次下載 2.3GB，永久使用

### 6.4 為什麼 NIM 優先

- 成本最低（NIM < Minimax << Claude）
- Phase 1 結束評估實際成功率，必要時改 Claude 優先
- Issue #001 追蹤

### 6.5 為什麼三向量分層

- 解決舊專案「所有東西混嵌入」的根本問題
- 元資料用 SQL 過濾（精準）+ 語意用向量（容錯）+ 程式碼單獨向量（避免主導）
- 詳見 `docs/rag_design.md`

---

## 7. 環境變數規範（.env）

```bash
# === 必填 ===
ENVIRONMENT=development                      # development | production
TIMEZONE=Asia/Taipei

# === LLM API ===
NIM_API_KEY=...
NIM_BASE_URL=https://integrate.api.nvidia.com/v1
MINIMAX_API_KEY=...
MINIMAX_BASE_URL=https://api.minimax.chat/v1
ANTHROPIC_API_KEY=...

# === 路徑 ===
DATA_PATH=./data
SQLITE_PATH=./data/sqlite/main.db
DUCKDB_PATH=./data/duckdb/strategy_vectors.duckdb
HF_HOME=./data/models                        # bge-m3 快取

# === MC Bridge ===
MC_BRIDGE_HOST=127.0.0.1
MC_BRIDGE_PORT=8001
MC_DIR=C:/Program Files/TS Support/MultiCharts64
MC_STUDIES_DIR=C:/ProgramData/TS Support/MultiCharts64/StudyServer/Studies/SrcEl/Strategies

# === 預算 ===
DAILY_BUDGET_USD=10
HARD_STOP_USD=10

# === Web ===
API_PORT=8000
WEBUI_PORT=3000

# === Cloudflare（Phase 4 後）===
CF_TUNNEL_TOKEN=                             # 留空表示不啟用外網
```

---

## 8. 啟動流程（Phase 5 完成後）

```bash
# 1. MC Bridge（裸跑，必須先啟動）
cd D:\aismart
.\.venv\Scripts\activate
python -m src.core.mc_bridge.server
# 等待 "MC Bridge listening on 127.0.0.1:8001"

# 2. Docker 容器
docker-compose up -d
# app 會自動連線 host.docker.internal:8001

# 3. Cloudflare Tunnel（如有）
cloudflared tunnel run aismart
```

---

## 9. 已知限制與技術債清單

### 9.1 已知限制

- MC Bridge 必須手動啟動（Phase 1 採用，Issue #004 追蹤升級為 NSSM）
- 開發階段所有東西在筆電（搬遷到桌機後才 24/7）
- bge-m3 首次下載需要時間（~10 分鐘 / 視網速）

### 9.2 技術債

- 暫無

---

**END OF architecture.md**
