# API 契約文件

> **文件用途**：MC Bridge API 規格、Web API 規格、SSE 即時進度推送、認證設計
> **語言**：中文
> **適用對象**:Claude Code 在 Phase 3 (MC Bridge) 與 Phase 4 (Web UI) 必讀

---

## 1. 整體 API 架構

```
┌──────────────────────────────────────────────────┐
│  外部使用者（瀏覽器、太太手機）                    │
└──────────────────────────────────────────────────┘
            ↓ HTTPS
┌──────────────────────────────────────────────────┐
│  Cloudflare Tunnel / Port Forward (Issue #006)   │
└──────────────────────────────────────────────────┘
            ↓
┌──────────────────────────────────────────────────┐
│  webui:3000 (Nginx)                              │
│  ├─ 提供 React 靜態檔                            │
│  └─ proxy /api/* → app:8000                      │
└──────────────────────────────────────────────────┘
            ↓
┌──────────────────────────────────────────────────┐
│  app:8000 (FastAPI 主後端)                       │
│  ├─ /api/strategies/*                            │
│  ├─ /api/backtest/*                              │
│  ├─ /api/kpi/*                                   │
│  ├─ /api/budget/*                                │
│  └─ /api/settings/*                              │
└──────────────────────────────────────────────────┘
            ↓ host.docker.internal
┌──────────────────────────────────────────────────┐
│  mc_bridge:8001 (Windows 裸跑 FastAPI)           │
│  ├─ /api/health                                  │
│  ├─ /api/compile                                 │
│  └─ /api/extract_pla                             │
└──────────────────────────────────────────────────┘
```

---

## 2. MC Bridge API（Windows 裸跑微服務）

### 2.1 服務基本資訊

- **位址**：`http://127.0.0.1:8001`（容器內透過 `host.docker.internal:8001`）
- **協定**：HTTP（內網，不需 HTTPS）
- **認證**：無（內網環境，啟動時可選 token 驗證）
- **stateless**：所有狀態交給 app 容器

### 2.2 端點清單

| 方法 | 路徑 | 功能 |
|---|---|---|
| GET | `/api/health` | 健康檢查 |
| POST | `/api/compile` | 編譯 EL → PLA |
| GET | `/api/strategy_status` | 查 AI_TEST DLL 狀態 |
| POST | `/api/setup` | 一次性 setup（需 Admin） |

### 2.3 GET /api/health

**回應**：
```json
{
  "status": "ok",
  "mc_running": true,
  "pleditor_window_found": true,
  "ai_test_dll_exists": true,
  "version": "1.0.0",
  "uptime_seconds": 3600
}
```

### 2.4 POST /api/compile

最重要的端點：將 EL 編譯為 PLA 檔。

**Request**：
```json
{
  "strategy_id": "AI_S123",
  "el_code": "Inputs: ...; Vars: ...; ...",
  "timeout_seconds": 30
}
```

**成功回應**：
```json
{
  "status": "ok",
  "pla_file_path": "C:/data/pla_files/AI_S123.pla",
  "compile_time_ms": 1234,
  "el_file_path": "C:/ProgramData/.../AI_TEST.el",
  "dll_updated": true
}
```

**編譯失敗回應**：
```json
{
  "status": "compile_error",
  "errors": [
    {
      "line": 12,
      "column": 5,
      "severity": "error",
      "message": "Unknown identifier 'Volum'",
      "raw": "Error: Unknown identifier 'Volum' at line 12, column 5"
    }
  ],
  "compile_time_ms": 850
}
```

**系統錯誤回應**：
```json
{
  "status": "system_error",
  "error_type": "mc_not_running" | "pleditor_window_lost" | "timeout" | "unknown",
  "message": "MC 視窗找不到，請手動重啟 MC",
  "details": "..."
}
```

### 2.5 GET /api/strategy_status

```json
{
  "ai_test_dll_exists": true,
  "ai_test_dll_modified": "2026-04-28T10:23:00",
  "studies_dir_writable": true,
  "needs_setup": false
}
```

### 2.6 POST /api/setup（需 Admin）

一次性設定。**如果服務本身不是 Admin 啟動，這個會回 403**。

```json
{
  "el_code_template": "..."  // 預設 MA Cross 模板
}
```

回應：成功訊息或詳細錯誤。

### 2.7 內部實作參考

詳見 `docs/legacy/original_source/mc_bridge.py`（從舊專案 1261 行 copy）。重要函式：

- `_load_mc_config()`：讀取 MC 路徑與設定
- `_close_mc_dialogs()`：背景守衛 thread
- `_clean_el_for_pleditor()`：cp950 清理
- `_is_strategy_registered()`：檢查 DLL 存在
- `setup_aismart_template()`：一次性設定
- `compile_el()`：核心編譯函式

---

## 3. Web API（FastAPI 主後端）

### 3.1 服務基本資訊

- **位址**：容器內 `http://app:8000`，外網經由 webui proxy
- **協定**：HTTPS（外網）/ HTTP（內網）
- **認證**：API Key（內部用）+ Cloudflare Access Email 白名單（外網用）
- **OpenAPI**：自動產出於 `/docs`

### 3.2 端點分組

```
/api/strategies/*       策略 CRUD + 生成
/api/backtest/*         回測觸發 + 結果查詢
/api/kpi/*              KPI 看板數據
/api/budget/*           預算狀態
/api/settings/*         系統設定（爬蟲黑白名單、LLM、ideas）
/api/quality/*          品質指標查詢
/api/health             健康檢查
```

### 3.3 認證

#### 3.3.1 內部 API Key（給程式呼叫）

```
HTTP Header:
  X-API-Key: <api_key_from_env>
```

#### 3.3.2 外網存取（Phase 4 後）

由 Cloudflare Access 處理 Email 白名單，後端只需驗證 Cloudflare 注入的 header：

```
HTTP Header:
  Cf-Access-Authenticated-User-Email: user@example.com
```

詳見 `docs/architecture.md` §6 與 Issue #006。

### 3.4 策略 API

#### POST /api/strategies/generate

啟動策略生成（**非同步、回 task_id**）。

```json
// Request
{
  "user_prompt": "我想做一個夜盤的均值回歸策略，5 分 K，停損 30 點",
  "batch_size": 1,            // 1 ~ 50
  "force_diversify": false
}

// Response
{
  "task_id": "task_abc123",
  "status": "queued",
  "estimated_duration_seconds": 60
}
```

#### GET /api/strategies/generate/{task_id}/stream（SSE）

即時進度推送（Server-Sent Events）。

```
GET /api/strategies/generate/task_abc123/stream
Accept: text/event-stream

→ 回應流：

data: {"step": "step1_intent", "status": "running", "progress": 0.10}

data: {"step": "step1_intent", "status": "done", "progress": 0.20, "result": {...}}

data: {"step": "step2_rag", "status": "running", "progress": 0.30}

...

data: {"step": "complete", "status": "done", "progress": 1.0, "strategy_id": 456}

data: [DONE]
```

#### GET /api/strategies

列出策略（含篩選 / 排序）。

```
GET /api/strategies?
    trading_session=daytrade_day&
    logic_type=trend&
    sharpe_min=1.5&
    sort_by=sharpe&
    order=desc&
    page=1&page_size=20

// Response
{
  "items": [
    {
      "id": 123,
      "name": "TXDay_MA_Cross_001",
      "trading_session": "daytrade_day",
      "logic_type": "trend",
      "sharpe": 1.85,
      "max_drawdown": 0.22,
      "profit_factor": 1.67,
      "total_trades": 234,
      "win_rate": 0.585,
      "overfitting_flag": false,
      "created_at": "2026-04-28T10:23:00"
    }
  ],
  "total": 100,
  "page": 1,
  "page_size": 20
}
```

#### GET /api/strategies/{id}

策略詳情（用於詳情頁）。

```json
{
  "id": 123,
  "name": "...",
  "yaml_content": "...",
  "el_code": "...",
  "pla_file_path": "...",
  
  "performance": {
    "sharpe": 1.85,
    "max_drawdown": 0.22,
    "profit_factor": 1.67,
    "total_trades": 234,
    "win_rate": 0.585,
    "avg_holding_minutes": 47,
    "data_range": "2014-01 ~ 2025-12"
  },
  
  "wfa_summary": {
    "avg_is_sharpe": 2.1,
    "avg_oos_sharpe": 1.6,
    "oos_is_ratio": 0.76,
    "overfitting_flag": false,
    "windows_count": 21
  },
  
  "wfa_windows": [
    {
      "window_index": 1,
      "is_start": "2014-01-01",
      "is_end": "2015-06-30",
      "oos_start": "2015-07-01",
      "oos_end": "2015-12-31",
      "is_sharpe": 1.92,
      "oos_sharpe": 1.45
    }
  ],
  
  "equity_curve": [
    {"date": "2014-01-02", "equity": 100000},
    {"date": "2014-01-03", "equity": 100250}
  ],
  
  "report_markdown": "# 策略報告..."
}
```

#### GET /api/strategies/{id}/pla

下載 PLA 檔。

```
GET /api/strategies/123/pla
→ Binary download (Content-Type: application/octet-stream)
```

#### GET /api/strategies/{id}/report

下載策略 Markdown 報告。

#### POST /api/strategies/{id}/mark_for_review

標記為待人工審核（給 overfitting_flag=true 的策略）。

### 3.5 回測 API

#### POST /api/backtest/rerun/{strategy_id}

重跑某個策略的回測。

#### GET /api/backtest/queue

當前回測佇列狀態。

```json
{
  "running": [{"strategy_id": 123, "engine": "backtrader", "progress": 0.4}],
  "queued": [...],
  "estimated_remaining_minutes": 45
}
```

### 3.6 KPI 看板 API

#### GET /api/kpi/dashboard

```json
{
  "strategy_count": 234,
  "this_week_added": 12,
  "avg_sharpe": 1.45,
  "max_drawdown_avg": 0.31,
  "pending_review_count": 8,
  "overfitting_count": 23,
  
  "session_distribution": {
    "daytrade_day": 80,
    "daytrade_night": 60,
    "swing_day": 50,
    "swing_full": 44
  },
  
  "logic_type_distribution": {
    "trend": 110,
    "mean_reversion": 70,
    "tunnel": 40,
    "pattern": 14
  }
}
```

### 3.7 預算 API

#### GET /api/budget/status

```json
{
  "today_used_usd": 2.34,
  "today_limit_usd": 10.00,
  "today_used_percent": 0.234,
  "current_mode": "normal",  // normal | throttle | survival | hard_stop
  
  "this_week_total_usd": 8.50,
  
  "by_provider": {
    "nim": 0.05,
    "minimax": 1.20,
    "claude": 1.09
  },
  
  "by_step": {
    "step1_intent": 0.30,
    "step3_skeleton": 0.85,
    "step4_el": 0.95,
    "step5_critique": 0.24
  }
}
```

### 3.8 系統設定 API

#### 爬蟲黑白名單

```
GET /api/settings/crawler/whitelist
GET /api/settings/crawler/blacklist
PUT /api/settings/crawler/whitelist
PUT /api/settings/crawler/blacklist
```

#### LLM 設定

```
GET /api/settings/llm/providers
PUT /api/settings/llm/providers
```

#### Strategy Ideas

```
GET /api/settings/ideas              // 列出已輸入的 ideas
POST /api/settings/ideas             // 新增 idea
DELETE /api/settings/ideas/{id}
```

### 3.9 品質指標 API

#### GET /api/quality/metrics

```json
{
  "rag_recall_at_5": 0.78,
  "rag_mrr": 0.52,
  "diversity_score_recent_batch": 0.45,
  "llm_success_rate": {
    "nim": 0.32,
    "minimax": 0.55,
    "claude": 0.85
  },
  "cost_per_passing_strategy_usd": 1.25,
  "el_validation_pass_rate": {
    "layer1": 0.92,
    "layer2": 0.78,
    "layer3": 0.65
  }
}
```

---

## 4. SSE 即時進度推送設計

### 4.1 為什麼用 SSE 而非 WebSocket

- **單向推送**：只是進度通知，不需要雙向
- **HTTP 標準**：Cloudflare Tunnel 完整支援
- **簡單**：前端 `EventSource` API 一行接收
- **不會被防火牆擋**

### 4.2 FastAPI 實作

```python
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
import asyncio
import json

app = FastAPI()

@app.get("/api/strategies/generate/{task_id}/stream")
async def stream_generation(task_id: str):
    """即時推送策略生成進度"""
    
    async def event_generator():
        # 從 Redis / 內部 queue 訂閱事件
        async for event in subscribe_task_events(task_id):
            data = {
                "step": event.step,           # step1_intent, step2_rag, ...
                "status": event.status,        # running, done, failed
                "progress": event.progress,    # 0.0 - 1.0
                "result": event.result,        # 該步驟的部分結果
                "timestamp": event.timestamp.isoformat()
            }
            yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
            
            if event.step == "complete":
                yield "data: [DONE]\n\n"
                break
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # 防止 Nginx buffering
        }
    )
```

### 4.3 React 前端接收

```typescript
// src/web/lib/strategyStream.ts
export function subscribeToGeneration(
  taskId: string,
  onEvent: (event: GenerationEvent) => void
) {
  const eventSource = new EventSource(
    `/api/strategies/generate/${taskId}/stream`,
    { withCredentials: true }
  );
  
  eventSource.onmessage = (e) => {
    if (e.data === "[DONE]") {
      eventSource.close();
      return;
    }
    const event = JSON.parse(e.data);
    onEvent(event);
  };
  
  eventSource.onerror = () => {
    console.error("SSE connection lost");
    eventSource.close();
  };
  
  return () => eventSource.close();
}
```

---

## 5. 錯誤處理與狀態碼

### 5.1 統一錯誤格式

所有錯誤回應遵循：

```json
{
  "error": {
    "code": "INVALID_INPUT" | "NOT_FOUND" | "BUDGET_EXHAUSTED" | ...,
    "message": "人類可讀的錯誤訊息",
    "details": {...}  // 可選
  }
}
```

### 5.2 常用 HTTP 狀態碼

| 碼 | 用途 |
|---|---|
| 200 | 成功 |
| 202 | 已接受（非同步任務）|
| 400 | 請求格式錯誤 |
| 401 | 認證失敗 |
| 403 | 無權限（如 Admin 限定） |
| 404 | 資源不存在 |
| 409 | 衝突（如重複名稱） |
| 422 | 驗證錯誤（業務規則違反） |
| 429 | 速率限制 |
| 500 | 伺服器錯誤 |
| 502 | MC Bridge 不可用 |
| 503 | 服務繁忙（如 LLM 排隊中） |

### 5.3 錯誤碼清單

```python
class ErrorCode(Enum):
    # 通用
    INVALID_INPUT = "INVALID_INPUT"
    NOT_FOUND = "NOT_FOUND"
    UNAUTHORIZED = "UNAUTHORIZED"
    
    # 業務
    STRATEGY_NAME_DUPLICATE = "STRATEGY_NAME_DUPLICATE"
    INVALID_TRADING_SESSION = "INVALID_TRADING_SESSION"
    
    # 預算
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    BUDGET_THROTTLED = "BUDGET_THROTTLED"
    
    # LLM
    LLM_ALL_PROVIDERS_FAILED = "LLM_ALL_PROVIDERS_FAILED"
    LLM_TIMEOUT = "LLM_TIMEOUT"
    
    # MC Bridge
    MC_BRIDGE_UNAVAILABLE = "MC_BRIDGE_UNAVAILABLE"
    MC_COMPILE_FAILED = "MC_COMPILE_FAILED"
    
    # EL 驗證
    EL_VALIDATION_FAILED = "EL_VALIDATION_FAILED"
```

---

## 6. 速率限制

### 6.1 內部 API 速率（容器內 app）

| 端點 | 限制 |
|---|---|
| POST /api/strategies/generate | 10 / 分鐘（防止使用者亂按） |
| GET /api/strategies | 60 / 分鐘 |
| 其他 GET | 不限 |

### 6.2 外網速率（Cloudflare 層）

由 Cloudflare 自動防護 DDoS。可額外設定：
- 同 IP 每分鐘 100 次請求
- 認證失敗 5 次後封鎖 1 小時

---

## 7. 雙容器部署的 webui ↔ app 通訊

### 7.1 docker-compose 內部網路

```yaml
# docker-compose.yml（節錄）
services:
  app:
    build: ./
    ports:
      - "8000:8000"   # 對主機暴露（除錯用）
    environment:
      - MC_BRIDGE_URL=http://host.docker.internal:8001
  
  webui:
    build: ./webui
    ports:
      - "3000:3000"   # 對外（Cloudflare Tunnel 指向這）
    environment:
      - API_BASE_URL=http://app:8000
```

### 7.2 webui 容器內 Nginx 設定

```nginx
# webui/nginx.conf
server {
    listen 3000;
    
    # React 靜態檔
    location / {
        root /usr/share/nginx/html;
        try_files $uri $uri/ /index.html;
    }
    
    # API proxy
    location /api/ {
        proxy_pass http://app:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        
        # SSE 必須關閉 buffering
        proxy_buffering off;
        proxy_cache off;
        proxy_set_header Connection "";
    }
}
```

---

## 8. OpenAPI 規格自動產出

FastAPI 自動產出：
- 開發環境：`http://localhost:8000/docs`（Swagger UI）
- 開發環境：`http://localhost:8000/redoc`（ReDoc）
- JSON 規格：`http://localhost:8000/openapi.json`

> **生產環境**：基於安全考量，`/docs` 與 `/redoc` 在生產環境關閉。

---

## 9. 開發階段的 API 模擬

### 9.1 前端開發時 mock app:8000

Phase 4 前期 React 開發可以用 mock：

```typescript
// webui/src/lib/api.ts
const API_BASE = import.meta.env.DEV
  ? "http://localhost:8000"   // dev: 連真實後端
  : "/api";                    // prod: 同 host
```

### 9.2 後端開發時 mock MC Bridge

寫單元測試時，用 fixture mock MC Bridge：

```python
# tests/conftest.py
@pytest.fixture
def mock_mc_bridge():
    return MagicMock(
        compile=MagicMock(return_value={
            "status": "ok",
            "pla_file_path": "/tmp/test.pla",
            "compile_time_ms": 100
        })
    )
```

---

**END OF api_contracts.md**
