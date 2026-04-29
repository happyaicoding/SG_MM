# Phase 計畫總覽

> **文件用途**：完整 Phase 0-5 + 搬遷階段詳細規劃
> **語言**：中文
> **適用對象**：Claude Code 進入每個 Phase 開始時必讀對應章節

---

## 0. 整體 Phase 概覽

| Phase | 名稱 | 工時 | 期程 | 風險 |
|---|---|---|---|---|
| **Phase 0** | 對齊與重用清理 | 10h | 1 週 | 🟢 低 |
| **Phase 1** | 基礎設施與資料層 | 18h | 1.5 週 | 🟢 低 |
| **Phase 2** | 回測引擎與業務規則 | 30h | 2.5 週 | 🟡 中 |
| **Phase 3** | LLM 編排與 EL 生成 | 30h | 2.5 週 | 🟠 中（已驗證 PoC） |
| **Phase 4** | Web UI 與外網存取 | 35h | 3 週 | 🟢 低 |
| **Phase 5** | 整合、爬蟲與自動化 | 22h | 2 週 | 🟢 低 |
| 搬遷 | 部署到桌機 | 13-21h | 3-5 天 | 🟡 中 |
| **總計** | | **158-166h** | **約 13 週** | |

每週 12.5 小時 × 13 週 = 162.5 小時，**完美對齊**。

### 0.1 關鍵里程碑

```
W1     Phase 0 完成      ─→ 新 repo 建好、舊資產整理完
W2-3   Phase 1 完成      ─→ 資料層可用、向量庫可查
W4-6   Phase 2 完成      ─→ 雙引擎跑通、WFA 可用
W7-9   Phase 3 完成      ─→ ★ 一句話 → PLA 端到端通 ★
W10-12 Phase 4 完成      ─→ Web UI 可用、太太能看
W13    Phase 5 完成      ─→ 全自動化、爬蟲入庫
W14    搬遷完成          ─→ 24/7 桌機運作
```

**最重要的里程碑：W9 結束 Phase 3** — 這時整個流水線跑通，後面都是錦上添花。

---

## Phase 0：對齊與重用清理（10h，1 週）

### 0.1 目標

把舊專案的可重用資產整理乾淨，建立新專案骨架。**不啟動任何功能**，只確保 import 不爆。

### 0.2 為什麼有 Phase 0

避免「邊做邊改」走老路。Phase 0 是「**暖身與對齊**」，確保：
- 新專案目錄結構與 V1.4 規格 100% 對齊
- 舊專案的 Layer 1 程式碼整理進新位置
- 開發環境 Docker / uv / Git / pre-commit 全部就緒

### 0.3 模組清單與工時

| 模組 | 工時 |
|---|---|
| Git repo 初始化 + GitHub Private Repo 建立 + .gitignore | 1h |
| Docker Compose 骨架（app + webui，先空殼） | 3h |
| uv venv + pyproject.toml + 依賴鎖定 | 1h |
| 從舊專案抽取 Layer 1 程式碼到新 repo（用 setup_legacy_assets.py） | 3h |
| 寫 docs/legacy_assets_inventory.md（重用資產清單） | 2h |

### 0.4 詳細任務

#### 0.4.1 Git repo 初始化（1h）

```bash
# 在筆電上
mkdir aismart && cd aismart
git init -b main
git remote add origin git@github.com:USER/aismart.git

# 建立 .gitignore
cat > .gitignore << 'EOF'
# Python
__pycache__/
*.pyc
.venv/
.pytest_cache/
.ruff_cache/

# Data
data/
*.db
*.duckdb
*.csv
*.pla
*.dll

# IDE
.vscode/
.idea/

# Env
.env

# Models
models/
.cache/
EOF

# 初始 commit（含 V1.4 文件包）
git add CLAUDE.md docs/
git commit -m "[Phase0] chore: initial V1.4 documentation"
git push -u origin main
```

#### 0.4.2 Docker Compose 骨架（3h）

建立 `docker-compose.yml`：

```yaml
version: "3.9"
services:
  app:
    build:
      context: .
      dockerfile: docker/app.Dockerfile
    container_name: aismart_app
    ports:
      - "8000:8000"
    volumes:
      - ./data:/app/data
      - ./config:/app/config
      - ./logs:/app/logs
    environment:
      - PYTHONUNBUFFERED=1
      - MC_BRIDGE_URL=http://host.docker.internal:8001
    env_file: .env
    extra_hosts:
      - "host.docker.internal:host-gateway"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/api/health"]
      interval: 30s
  
  webui:
    build:
      context: ./webui
      dockerfile: Dockerfile
    container_name: aismart_webui
    ports:
      - "3000:3000"
    depends_on:
      - app
```

`docker/app.Dockerfile`：

```dockerfile
FROM python:3.11-slim
WORKDIR /app

# 安裝 uv
RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
RUN uv pip install --system -e . --no-cache

COPY src/ ./src/
COPY scripts/ ./scripts/
COPY config/ ./config/

CMD ["uvicorn", "src.web.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

#### 0.4.3 uv venv + pyproject.toml（1h）

```toml
# pyproject.toml
[project]
name = "aismart"
version = "0.1.0"
requires-python = ">=3.11"

dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "pydantic>=2.9",
    "pyyaml>=6.0",
    "python-dotenv>=1.0",
    
    # Data
    "pandas>=2.2",
    "numpy>=1.26",
    
    # DB
    "duckdb>=0.10",
    
    # Embedding
    "sentence-transformers>=3.0",
    "torch>=2.4",
    
    # LLM
    "anthropic>=0.39",
    "openai>=1.50",          # NIM 用 OpenAI 相容介面
    "httpx>=0.27",
    
    # Backtest
    "vectorbt>=0.26",
    "backtrader>=1.9",
    
    # Web crawler (Phase 5)
    "beautifulsoup4>=4.12",
    "lxml>=5.3",
    "apscheduler>=3.10",
    
    # Windows native (only for MC Bridge venv)
    # pywin32, pywinauto, psutil → 不放進主依賴
]

[project.optional-dependencies]
dev = [
    "pytest>=8.3",
    "pytest-asyncio>=0.24",
    "pytest-cov>=5.0",
    "black>=24.10",
    "ruff>=0.7",
    "mypy>=1.13",
    "pre-commit>=4.0",
]

mc_bridge = [
    "pywin32>=308; platform_system == 'Windows'",
    "pywinauto>=0.6.9; platform_system == 'Windows'",
    "psutil>=6.1; platform_system == 'Windows'",
]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.black]
line-length = 100
target-version = ["py311"]
```

```bash
uv venv
uv pip install -e ".[dev]"

# Windows 主機（MC Bridge venv 獨立）
mkdir mc_bridge_env
cd mc_bridge_env
uv venv
uv pip install -e "..[mc_bridge]"
```

#### 0.4.4 抽取 Layer 1 程式碼（3h）

寫 `scripts/setup_legacy_assets.py`：

```python
"""
Phase 0：從 docs/legacy/original_source/ 抽取 Layer 1 程式碼到新位置。

用法：python scripts/setup_legacy_assets.py
"""
import shutil
from pathlib import Path

LEGACY_ROOT = Path("docs/legacy/original_source")
NEW_ROOT = Path("src")

LAYER_1_MAPPING = [
    # (legacy_path, new_path, post_process)
    ("src/core/backtest/mc_bridge.py", "src/core/mc_bridge/_legacy_combined.py", None),
    ("src/core/ai_engine/client.py", "src/core/ai_engine/client.py", None),
    ("src/core/ai_engine/vector_store.py", "src/core/ai_engine/vector_store.py", None),
    ("src/core/ai_engine/embedder.py", "src/core/ai_engine/embedder.py", None),
    ("src/core/backtest/python_bt.py", "src/core/backtest/vectorbt_filter.py", None),
    ("scripts/verify_mc_connection.py", "scripts/verify_mc_connection.py", None),
    ("scripts/index_library.py", "scripts/index_library.py", None),
    ("library/Daytrade/TXDTA505.ELS", "library/Daytrade/TXDTA505.ELS", None),
    ("library/Daytrade/TXDTA505.yaml", "library/Daytrade/TXDTA505.yaml", None),
]

def main():
    for legacy_rel, new_rel, _ in LAYER_1_MAPPING:
        src = LEGACY_ROOT / legacy_rel
        dst = Path(new_rel)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        print(f"[OK] {legacy_rel} → {new_rel}")
    
    print("\nLayer 1 程式碼抽取完成！")
    print("接下來：python -m pytest tests/ --collect-only 確認 import 不爆")

if __name__ == "__main__":
    main()
```

#### 0.4.5 寫 docs/legacy_assets_inventory.md（2h）

完整列出舊專案中：
- 哪些檔案 Layer 1 直接重用
- 哪些 Layer 2 重構對齊
- 哪些 Layer 3 全新開發
- 已知陷阱（cp950、emoji print、Admin 設定流程）
- 沿用的技術選型理由

模板：

```markdown
# 舊專案資產清單與重用策略

> 本文件記錄從舊專案（aismart_legacy）抽取的可重用資產。
> Phase 0 結束時鎖定，後續不再變動。

## Layer 1：直接重用（整段 copy）

### mc_bridge.py（1261 行 → src/core/mc_bridge/）
- 來源：legacy/src/core/backtest/mc_bridge.py
- 處置：copy 進 src/core/mc_bridge/_legacy_combined.py
- 計畫：Phase 3 拆成多個小模組（compiler、el_writer、dialog_guard、spr_parser、pla_extractor）
- 重用理由：含對話框守衛、cp950 處理、SPR 解析等難解問題

### client.py（463 行）
- 來源：legacy/src/core/ai_engine/client.py
- 處置：copy 進 src/core/ai_engine/client.py（不變）
- 計畫：Phase 3 加上 NIM 實作 + orchestrator 包裝

（其餘條目省略）

## Layer 2：重構對齊

（各條目）

## Layer 3：全新開發

（各條目）

## 已知陷阱

1. EL 檔必須 cp950 編碼寫入 Studies 目錄
2. Print 不能用 emoji
3. PLEditor 第一次需 Admin
4. ...
```

### 0.5 Phase 0 結束驗收

- [ ] `git status` 乾淨，所有檔案已 commit
- [ ] `docker-compose up` 兩個容器都能啟動且 health check 通過
- [ ] `uv pip list` 顯示所有依賴已安裝
- [ ] `python scripts/setup_legacy_assets.py` 執行成功
- [ ] `python -m pytest tests/ --collect-only` 不報 ImportError
- [ ] `docs/legacy_assets_inventory.md` 已寫完
- [ ] `docs/phase_reports/phase_0_report.md` 已寫完
- [ ] CLAUDE.md 的 Phase Status 已更新為「Phase 1 ⏳」

---

## Phase 1：基礎設施與資料層（18h，1.5 週）

### 1.1 目標

建立可運行的資料層 + 向量庫基礎建設。

### 1.2 模組清單與工時

| 模組 | 工時 | 備註 |
|---|---|---|
| .env 規劃 + 配置載入器 | 2h | API keys、路徑、預算 |
| SQLite Schema 建立 + migration | 4h | 含 strategies / backtest_results / wfa / llm_calls / failure_log / quality_metrics 等 |
| CSV ETL（含 trading_day 計算） | 5h | 「資料本身判定」邏輯 |
| DuckDB 初始化 + 4 個 Collection | 3h | 含三向量 schema + HNSW 索引 |
| RAG test set 建立（30 個 cases） | 2h | 為 Phase 1 結束的品質評估準備 |
| Unit test（trading_day 邊界、ETL 正確性） | 1.5h | |
| Phase 1 工作說明書 | 0.5h | |
| **合計** | **18h** | |

### 1.3 關鍵設計重點

#### 1.3.1 trading_day 邏輯實作

詳見 `docs/business_rules.md` §2.3。重點：**不依賴節假日表**，從資料本身判定。

#### 1.3.2 三向量 schema 嚴格實作

詳見 `docs/rag_design.md` §3。Phase 1 必須建好 3 個 HNSW 索引，即使 Collection 是空的。

#### 1.3.3 Quality Metric Test Set

`tests/rag_test_set.yaml` 包含至少 30 個「query → expected_ids」對照，用於後續評估。

### 1.4 Phase 1 結束驗收

- [ ] `python main.py db init` 跑通，建出所有 SQLite 表
- [ ] `python main.py data init --csv-dir data/csv/` 跑通，CSV → SQLite，總筆數合理
- [ ] 抽 5 個 trading_day 邊界日（連假前後、週末前後、夜盤跨日）人工驗證
- [ ] DuckDB 4 個 Collection 都能寫入並查詢 dummy 資料
- [ ] 三個 HNSW 索引存在
- [ ] `tests/rag_test_set.yaml` 有 ≥ 30 個 cases
- [ ] `pytest tests/unit/data/` 全綠
- [ ] Phase 1 工作說明書寫完

---

## Phase 2：回測引擎與業務規則（30h，2.5 週）

### 2.1 目標

建立可運行的雙引擎回測流水線，能讀 Phase 1 的資料、依台指期業務規則執行回測、產出 KPI。

### 2.2 模組清單與工時

| 模組 | 工時 |
|---|---|
| 業務規則引擎（時段、冷卻、強平） | 4h |
| 滑價計算（統一 4 點） | 1h |
| vectorbt 快篩引擎（沿用舊 python_bt + 業務規則整合） | 5h |
| backtrader WFA 引擎（IS 18 / OOS 6 / 步進 6） | 7h |
| KPI 計算模組（6 指標） | 3h |
| 篩選門檻判定 + WFA 合格判定 | 2h |
| 回測結果寫入 DB | 3h |
| 並行化（multiprocessing.Pool） | 2h |
| Unit test（含合成資料 case） | 2h |
| Phase 2 工作說明書 | 1h |
| **合計** | **30h** |

### 2.3 關鍵設計

#### 2.3.1 業務規則整合
詳見 `docs/business_rules.md` §4 + §9。**在 vectorbt 與 backtrader 兩個引擎中都要套用**，確保結果一致。

#### 2.3.2 dev_data_range 機制

`config/backtest.yaml` 的 `use_dev_range: true` 切換子集（4 年）加速 Phase 2 開發。

**Phase 2 結束前必須切回 false 跑一次完整 12 年驗證。**

#### 2.3.3 並行化注意事項

- backtrader 不能跨 process 共用 cerebro 物件
- 每個 process 獨立 load 資料 + 獨立執行
- 用 `multiprocessing.Pool(processes=cpu_count() - 2)` 留 CPU 給 OS

### 2.4 Phase 2 結束驗收

- [ ] vectorbt 跑 1 個 Few-shot 策略，KPI 結果與舊專案一致（誤差 < 5%）
- [ ] backtrader 跑同策略 21 個 WFA 窗口，結果寫入 SQLite
- [ ] 4 種 trading_session 各 1 個測試 case 通過
- [ ] 強平機制在 13:39 / 04:44 觸發點正確
- [ ] 滑價在每筆交易扣 4 點
- [ ] 50 個策略並行批次跑通（約 1-2 小時）
- [ ] Phase 2 工作說明書寫完

---

## Phase 3：LLM 編排與 EL 生成（30h，2.5 週）⚠️ 最高風險

### 3.1 目標

建立完整「使用者一句話 → PLA 檔」流水線。

### 3.2 四週切法

#### Week 1：MC Bridge 服務化（6h）

> **PoC 已驗證**（舊專案 mc_bridge.py 1261 行），這裡是「服務化」工作。

| 任務 | 工時 |
|---|---|
| 把舊 mc_bridge.py 拆成獨立模組 | 3h |
| 包成 FastAPI 服務（:8001） | 2h |
| 端到端測試：Python 呼叫 → 編譯 → PLA | 1h |

**Week 1 結束驗收**：能從 app 容器透過 `host.docker.internal:8001` 編譯一個簡單 EL，產 PLA 檔。

#### Week 2：LLM 編排 + 三層驗證 Layer 1（10h）

| 任務 | 工時 |
|---|---|
| 三家 LLM client 統一介面（沿用舊 client.py + 加 NIM） | 3h |
| 儲備遞進編排器 | 2h |
| 預算控制（三段式降級）| 3h |
| EL 第 1 層規則檢查（12 條規則 + unit test）| 2h |

#### Week 3：5 段 Prompt 設計 + Few-shot 範例庫（8h）

> 這週的 5h **是使用者親自手寫**，Claude Code 只負責設計指引。

| 任務 | 工時 | 誰 |
|---|---|---|
| Claude Code 產出 docs/few_shot_design_guide.md | 1h | Claude Code |
| 使用者手寫 5-10 個 Few-shot 範例 | 5h | 使用者 |
| 5 個 prompt 模板（step1-5）撰寫 | 2h | Claude Code |

#### Week 4：整合 + EL 三層驗證 Layer 2/3 + 失敗回流（6h）

| 任務 | 工時 |
|---|---|
| Layer 2 LLM 自我審核 | 2h |
| Layer 3 MC 試編譯整合（接 Week 1 服務） | 1h |
| 失敗回流邏輯（最多 3 次 retry） | 2h |
| 端到端整合測試（一句話 → PLA + 報告） | 1h |

### 3.3 Phase 3 結束驗收

- [ ] Week 1 PoC：手寫 EL → MC 編譯成功
- [ ] 三家 LLM 都能呼叫，預算控制有效
- [ ] EL 三層驗證能攔截錯誤，且失敗回流可修正
- [ ] **端到端**：輸入「我想要一個夜盤均值回歸策略」→ 產出可用 PLA 檔
- [ ] 5-10 個 Few-shot 範例已入庫並嵌入
- [ ] 所有 LLM 呼叫都記錄在 `llm_calls` 表（含成本）
- [ ] Phase 3 工作說明書

### 3.4 風險控制

| 風險 | 應對 |
|---|---|
| MC COM 偶爾凍結 | watchdog 重啟（沿用舊 dialog_guard） |
| EL 失敗率過高（NIM < 30%）| 觸發 Issue #001，改 Claude 優先 |
| Few-shot 不夠專業 | 使用者親自寫，品質保證 |
| 預算超出 | 三段式降級自動處理 |

---

## Phase 4：Web UI 與外網存取（35h，3 週）

### 4.1 目標

建立 V1.3 第 2.5 節規定的 Web UI，並建立外網入口（給太太手機看）。

### 4.2 模組清單與工時

| 模組 | 工時 |
|---|---|
| FastAPI 後端 API 設計 + OpenAPI 規格 | 4h |
| API 實作（策略、回測、KPI、成本、品質、設定） | 7h |
| React 專案初始化（Vite + shadcn/ui） | 2h |
| KPI 看板 + 排行表 | 4h |
| 策略詳情頁（EL viewer + 資產曲線 + WFA 圖） | 6h |
| 軟體設定介面（爬蟲、ideas、LLM） | 5h |
| 策略生成 prompt 介面（SSE 即時進度） | 4h |
| 預算 + 品質指標儀表板 | 1h |
| **外網方案實作（Issue #006 決策後）** | 1h |
| 認證（FastAPI API Key + Cloudflare Access） | 1h |
| **合計** | **35h** |

### 4.3 Phase 4 結束驗收

- [ ] 6 個 UI 區塊都能用
- [ ] 太太手機（4G）能看到 Web UI
- [ ] 認證機制有效
- [ ] SSE 即時進度顯示流暢
- [ ] 預算 + 品質指標儀表板完整
- [ ] Phase 4 工作說明書

---

## Phase 5：整合、爬蟲與自動化（22h，2 週）

### 5.1 目標

把所有 Phase 1-4 串接，補上爬蟲與排程，整個系統可長時間自動運行。

### 5.2 模組清單與工時

| 模組 | 工時 |
|---|---|
| PTT 爬蟲 | 5h |
| Mobile01 爬蟲 | 4h |
| 黑白名單機制 | 2h |
| 去重（URL hash + 內容 hash） | 2h |
| 自動嵌入入 DuckDB | 2h |
| APScheduler 排程 | 2h |
| 策略說明報告產生器 | 3h |
| 端到端整合測試 | 1h |
| 多樣性指標自動計算 + 警報 | 0.5h |
| Phase 5 工作說明書 | 0.5h |
| **合計** | **22h** |

### 5.3 Phase 5 結束驗收

- [ ] 爬蟲跑一次抓到至少 50 篇 PTT 文章入庫
- [ ] 黑白名單修改即時生效
- [ ] 排程跑兩天無例外
- [ ] 端到端：一句話 → PLA + 策略報告
- [ ] 多樣性指標自動計算
- [ ] Phase 5 工作說明書

---

## 搬遷階段（13-21h，3-5 天）

### 6.1 Day 1：桌機環境準備（4-6h）

- [ ] 安裝 Docker Desktop
- [ ] 安裝 Python 3.11+ 與 uv
- [ ] 安裝 Git（含 GitHub 認證）
- [ ] 桌機 MultiCharts 確認版本一致
- [ ] 確認硬體規格（8 核 / 32GB / 500GB SSD）✅ 你已具備

### 6.2 Day 2：資料搬遷（3-4h）

- [ ] 筆電 `docker-compose down`
- [ ] 壓縮 `data/` 資料夾
- [ ] 透過 USB / 區網傳到桌機
- [ ] 桌機解壓到對應路徑

### 6.3 Day 3：服務啟動（3-5h）

- [ ] 桌機 `git clone`
- [ ] 桌機 `uv venv` + 安裝
- [ ] 桌機 `docker-compose up --build`
- [ ] MC Bridge 裸跑啟動（手動）
- [ ] Smoke test：
  - `verify_mc_connection.py` 全綠
  - 端到端：產 1 個策略 → PLA → 報告

### 6.4 Day 4：外網設定（2-4h，依 Issue #006）

- [ ] 設定 Cloudflare Tunnel（建議）或 Port Forward
- [ ] 設定 SSL 憑證
- [ ] 設定認證白名單
- [ ] 太太手機驗證可連線

### 6.5 Day 5：穩定性觀察（1-2h 監控）

- [ ] 觀察 24 小時 log
- [ ] 觀察排程是否正常
- [ ] 寫 `docs/deployment_guide.md`

### 6.6 收尾

- [ ] 部署成功確認
- [ ] 筆電專案標記為「歷史備份」（保留但不再開發）

---

## 7. Phase 1 啟動 Prompt（給 Claude Code）

> **複製以下整段給 Claude Code，即可啟動 Phase 1**：

```
你現在是 AISMART 專案的 Claude Code 執行者。請按以下順序執行 Phase 1：

## Step 1：環境確認
1. cat CLAUDE.md  → 完整讀完
2. cat docs/quality_safeguards.md  → 完整讀完
3. 在 docs/phase_reports/phase_1_report.md 寫入 Phase 1 入口檢查（依 quality_safeguards.md §1.1 模板）
4. git commit -m "[Phase1] chore: phase entry quality check"

## Step 2：閱讀本 Phase 規範
1. cat docs/phases_plan.md  → 讀 Phase 1 章節
2. cat docs/business_rules.md §2  → 讀 trading_day 邏輯
3. cat docs/rag_design.md §3  → 讀多向量 schema
4. cat docs/architecture.md §5  → 讀 Schema 設計

## Step 3：依模組順序實作

### 3.1 .env 規劃 + 配置載入器（2h）
- 寫 `.env.example`（依 docs/architecture.md §7）
- 寫 `src/core/config.py`（pydantic 配置類）
- 寫 unit test：`tests/unit/test_config.py`

### 3.2 SQLite Schema 建立 + migration（4h）
- 寫 `src/core/db.py`（SQLite + DuckDB 連線管理）
- 寫 SQL migration 腳本 `scripts/migrate_db.py`
- 完整 Schema 依 docs/architecture.md §5
- 寫 unit test 確認 schema 正確

### 3.3 CSV ETL（5h）
- 寫 `src/core/data/trading_day.py`（依 business_rules.md §2.3）
- 寫 `src/core/data/etl.py`（CSV → SQLite，含 trading_day 計算）
- 寫 unit test 涵蓋 5 個邊界 case：
  * 連假前的週五夜盤
  * 連假後的週一日盤
  * 跨週末的夜盤
  * 颱風假當日
  * 一般日盤/夜盤
- main.py 加入 `data init` 指令

### 3.4 DuckDB 初始化 + 4 Collection（3h）
- 從 docs/legacy/original_source/src/core/ai_engine/vector_store.py copy 進 src/core/ai_engine/vector_store.py
- 改寫支援三向量 schema（依 rag_design.md §3.3）
- 寫 4 個 Collection 的 init 腳本
- 建立 HNSW 索引
- 沿用 scripts/index_library.py（已從 legacy copy）

### 3.5 RAG test set 建立（2h）
- 寫 `tests/rag_test_set.yaml`，至少 30 個 cases
- 從 library/Daytrade/TXDTA505 入手（已有的 Few-shot）
- 從舊專案 library/ 推導其他 cases

### 3.6 Unit test（1.5h）
- 重點：trading_day 邊界、ETL 正確性
- 跑 `pytest tests/unit/data/`，確認全綠

### 3.7 Phase 1 工作說明書（0.5h）
- 完成 docs/phase_reports/phase_1_report.md
- 內容依 spec.md §5.9

## Step 4：Phase Gate 驗收

依 docs/phases_plan.md §1.4 跑完所有驗收項目：

- [ ] python main.py db init
- [ ] python main.py data init --csv-dir data/csv/
- [ ] 抽 5 個 trading_day 邊界日人工驗證
- [ ] DuckDB 4 Collection 可寫可查
- [ ] 三個 HNSW 索引存在
- [ ] tests/rag_test_set.yaml ≥ 30 cases
- [ ] pytest 全綠
- [ ] phase_1_report.md 寫完

## Step 5：規格紀律（重要！）

每完成一個模組：
1. git commit -m "[Phase1] feat: <module>"
2. CLAUDE.md 的 Phase Status 更新進度

如果遇到不確定：
- ⛔ 不要猜
- ⛔ 不要做 Phase 範圍外的事
- ✅ 停下來問使用者

如果想合併步驟、跳過驗證、或為了快速 demo 走捷徑：
- ⛔ 拒絕
- ✅ 先寫進 Issue List，等該 Phase 結束評估

## 開始執行
從 Step 1 開始。完成 Step 1 後回報，等使用者確認再繼續 Step 2。
```

---

## 8. 整合產出清單

V1.4 文件包總共 **10 份**：

| # | 檔案 | 角色 |
|---|---|---|
| 1 | CLAUDE.md | Claude Code 主控檔（英文，每次必讀） |
| 2 | docs/spec.md | V1.4 主規格 |
| 3 | docs/business_rules.md | 台指期業務規則 |
| 4 | docs/architecture.md | 系統架構 + 三層重用清單 |
| 5 | docs/llm_prompts.md | 5 段 prompt + Few-shot 設計 |
| 6 | docs/rag_design.md | 多向量檢索設計 |
| 7 | docs/el_validation.md | EL 三層驗證 |
| 8 | docs/api_contracts.md | MC Bridge + Web API |
| 9 | docs/quality_safeguards.md | 10 品質機制（每 Phase 必讀！） |
| 10 | docs/phases_plan.md | 本文件 |

加上 **Issue List**（`docs/issues_to_review.md`，10 個追蹤項）。

---

**END OF phases_plan.md**
