# CLAUDE.md — AISMART AI 自動台指期策略開發系統

## 專案概覽

台指期（TX）AI 自動化策略開發平台。

- **資料**：2014–2025 OHLC 1分K，約 259 萬根 bar（無成交量）
- **AI 引擎**：Claude API（claude-sonnet-4-20250514）
- **回測**：vectorbt（Python 初篩）→ MultiCharts（精測，Phase 2）
- **介面**：OpenClaw（訊息操作）+ FastAPI Web API（port 8388）
- **開發進度**：Phase 1 完成 ✦ Phase 2 AI 生成引擎進行中

---

## 快速啟動

```bash
python main.py db init                           # 初始化 SQLite（首次執行）
python main.py data init --csv-dir data/raw/     # 載入 CSV 到 DuckDB
python main.py web --port 8388                   # 啟動 FastAPI
python main.py backtest --strategy MA_Cross      # CLI 回測（名稱或 UUID）
python main.py backtest --strategy list          # 查看所有可用策略
python main.py backtest --strategy MA_Cross --timeframe 60m --start 2020-01-01 --end 2020-12-31
python main.py run --mode full --cycles 10       # 全自動開發循環
python main.py report --strategy <id> --format html
```

---

## 目錄結構

```
AISMART/
├── src/
│   ├── core/
│   │   ├── data/          loader.py / store.py(DuckDB) / validator.py
│   │   │                  resample.py / duckdb_helper.py
│   │   ├── backtest/      base_strategy.py / python_bt.py / runner.py
│   │   │                  metrics.py / wfa.py / mc_bridge.py(stub)
│   │   └── ai_engine/     client.py / generator.py / optimizer_ai.py
│   │                      researcher.py / prompt_templates/
│   ├── db/                models.py / init_db.py
│   ├── api/               app.py(FastAPI)
│   ├── interfaces/        html_report.py / pdf_report.py / openclaw/
│   └── strategies/        ma_cross.py / rsi_reversal.py / registry.py
├── data/
│   ├── raw/               *.csv（TX 1分K，格式 A 或格式 B 均可）
│   └── aismart.db         SQLite（strategies / backtest_results / alerts）
├── db/                    market.duckdb（DuckDB 市場資料）
├── openclaw_skill/        index.js / skill.json
├── config.yaml
└── main.py
```

---

## 核心規範

### 策略介面（所有策略必須繼承）

```python
from src.core.backtest.base_strategy import BaseStrategy

class MyStrategy(BaseStrategy):
    NAME     = "My_Strategy"
    CATEGORY = "trend"          # trend / mean_reversion / opening / scalp
    PARAMS   = {"fast_ma": 10, "slow_ma": 30}   # 參數 ≤ 5 個

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        """回傳 pd.Series：1=做多, -1=做空, 0=不動。
        必須 shift(1) 避免 lookahead bias（下一根開盤進場）。"""
        ...

    def metadata(self) -> dict: ...
    def validate_params(self, params: dict) -> bool: ...
```

### 策略 Registry

```python
from src.strategies.registry import get_strategy, list_strategies

cls = get_strategy("MA_Cross")          # 依名稱取得策略類別
metas = list_strategies()               # 列出所有已註冊策略
```

新策略在 `src/strategies/registry.py` 的 `_REGISTRY` 字典中登記。

### 資料存取

```python
from src.core.data.store import DataStore

with DataStore() as store:
    df = store.query("TX", start="2020-01-01", end="2020-12-31")
```

**一律透過 `DataStore`，不直接讀 CSV。**

### 資料重採樣

```python
from src.core.data.resample import resample_ohlc

df_60m = resample_ohlc(df_1m, "60m")    # 60 分K
df_90m = resample_ohlc(df_1m, "90m")    # 90 分K（自訂）
df_day = resample_ohlc(df_1m, "D")      # 日K
# 支援：Nm（分鐘）/ Nh（小時）/ 純數字（視為分鐘）/ D
```

### 合約參數（config.yaml 可覆寫）

```python
CONTRACT_SIZE   = 200        # 每點 NT$200
COMMISSION      = 100        # 單邊手續費（元）
SLIPPAGE        = 1          # 點
INITIAL_CAPITAL = 500_000    # 初始資金
TRAIN = ("2015-01-01", "2021-12-31")
TEST  = ("2022-01-01", "2024-12-31")
```

### 篩選門檻（雙階段）

| 階段 | Sharpe | MaxDD | ProfitFactor | 交易次數 | OOS/IS |
|------|--------|-------|--------------|---------|--------|
| Python 初篩 | ≥ 1.2 | ≤ 35% | ≥ 1.0 | ≥ 80 | — |
| MC 精測 | ≥ 1.5 | ≤ 20% | ≥ 2.0 | ≥ 100 | ≥ 0.6 |

### Claude API

```python
MODEL = "claude-sonnet-4-20250514"
# 生成策略 max_tokens=4096，摘要/優化 max_tokens=2048
# Retry：最多 3 次，指數退避（1s / 4s / 16s）
# Rate limit：429 → 等待 60s
# researcher.py 啟用 web_search tool
```

### Power Language → Python 對照

| Power Language | Python |
|---|---|
| `Buy("L") next bar at market` | `signal = 1` |
| `Sell Short("S") next bar at market` | `signal = -1` |
| `ExitLong / ExitShort` | `signal = 0` |
| `XAverage(Close, N)` | `pandas_ta.ema(close, N)` |
| `Average(Close, N)` | `close.rolling(N).mean()` |
| `Highest(High,N)` / `Lowest(Low,N)` | `high.rolling(N).max()` / `low.rolling(N).min()` |
| `RSI(Close, N)` | `pandas_ta.rsi(close, N)` |
| `AvgTrueRange(N)` | `pandas_ta.atr(high, low, close, N)` |
| `Crosses Above` | `(s1>s2)&(s1.shift(1)<=s2.shift(1))` |
| `Close[1]` | `close.shift(1)` |

> `.eld` 檔案需從 MultiCharts IDE 另存為 `.txt` 才能讀取

---

## FastAPI 端點（port 8388）

```
GET  /health
GET  /strategies/available                # Registry 所有已註冊策略
POST /strategies/available/{name}/run     # 依名稱直接執行 Python 回測
POST /backtest/trigger                    # 對 DB 已存在策略執行回測
GET  /strategies                          # 策略清單（分頁 + 狀態過濾）
GET  /strategies/{id}                     # 策略詳情 + 最新回測結果
```

---

## OpenClaw Skill 整合

Skill 位於 `openclaw_skill/`，透過 HTTP 呼叫 FastAPI（port 8388）。

**支援指令（訊息傳入）：**

| 指令 | 動作 |
|---|---|
| `/run <N>` | 啟動 N 輪自動策略開發循環 |
| `/backtest <strategy_id>` | 對指定策略執行全期回測 |
| `/generate` | AI 生成一個新策略並回測 |
| `/optimize <strategy_id>` | AI 優化指定策略參數 |
| `/report <strategy_id>` | 回傳 HTML 報表連結 |
| `/list` | 列出策略庫與狀態 |
| `/status` | 顯示系統狀態（任務佇列、最近結果） |
| `/top` | 顯示當前績效最佳前 5 策略 |

---

## 注意事項

- **使用者下英文指令時，回復一律使用繁體中文**
- **資料讀取**：一律透過 `DataStore`，不直接讀 CSV
- **防過擬合**：Walk-Forward（訓練 24 月 → 測試 6 月滾動），參數 ≤ 5 個
- **時區**：全系統統一 `Asia/Taipei`
- **API Key**：存於 `.env`（`ANTHROPIC_API_KEY`），禁止寫死於程式碼
- **台指期時段**：日盤 08:45–13:45，夜盤 15:00–次日 05:00，需過濾異常 bar
- **Windows 相容**：Shell 指令使用 PowerShell；print() 輸出避免 emoji（cp950 編碼）
- **SQLite**：WAL mode 啟用，支援並發讀取；`data/aismart.db`
- **DuckDB**：市場資料存於 `db/market.duckdb`，透過 `DataStore` 存取
