# 台指期業務規則

> **文件用途**：本文件是系統實作業務邏輯的最終依據。Claude Code 修改任何時段、強平、滑價相關程式碼前必讀。
> **語言**：中文
> **參考**：V1.3 第 1 節 + V1.4 重大修訂

---

## 1. 台指期商品基本資訊

### 1.1 交易時段

```
週一 08:45 ── 13:45    日盤
週一 15:00 ── 週二 05:00  夜盤
週二 08:45 ── 13:45    日盤
...（依此循環）
週五 15:00 ── 週六 05:00  最後夜盤
週六、週日休市
```

### 1.2 點數價值

- **每點價值**：新台幣 200 元
- **單口最小變動**：1 點
- **單口賣方/買方損益**：點數差 × 200

### 1.3 滑價假設（V1.4 統一）

**所有交易**（含跨段、跨日）統一以 **4 點滑價**計算，金額為 NTD 800。

```python
# 全域常數
SLIPPAGE_POINTS = 4
SLIPPAGE_VALUE_NTD = SLIPPAGE_POINTS * 200  # 800

def calculate_trade_pnl(entry_price, exit_price, direction):
    """計算單筆交易損益（已扣滑價）"""
    raw_pnl_points = (exit_price - entry_price) * direction  # direction: 1=long, -1=short
    net_pnl_points = raw_pnl_points - SLIPPAGE_POINTS
    return net_pnl_points * 200  # NTD
```

> **未來擴充**：滑價是否改為跨日加倍（8 點）列為 Issue #002，由 Phase 4 後評估。屆時可改為 YAML 配置驅動。

---

## 2. 交易日定義（重要！）

### 2.1 核心規則

> **台指期交易日 T 包含三段**：
> 1. **夜盤前段**：T-1 自然日 15:00 ~ 23:59:59
> 2. **夜盤後段**：T 自然日 00:00 ~ 05:00
> 3. **日盤**：T 自然日 08:45 ~ 13:45

### 2.2 範例

```
範例 1：週二 (2025-01-14) 的交易日 T 包含
  ├─ 週一 2025-01-13  15:00-23:59  ← 夜盤前段
  ├─ 週二 2025-01-14  00:00-05:00  ← 夜盤後段
  └─ 週二 2025-01-14  08:45-13:45  ← 日盤

範例 2：跨週末 — 週一 (2025-01-13) 的交易日
  ├─ 週五 2025-01-10  15:00-23:59
  ├─ 週六 2025-01-11  00:00-05:00  ← 注意：跨週末
  └─ 週一 2025-01-13  08:45-13:45  ← 注意：跨過週六、週日

範例 3：連假 — 假設週四是國定假日
  ├─ 週三 15:00-23:59
  ├─ 週四 00:00-05:00 (但週四不是交易日)
  → 此時段資料應直接歸到下一個交易日（週五）
```

### 2.3 實作規則：以「資料本身判定」trading_day

> **不依賴節假日表**。直接從 1 分 K 資料中查「下一個有 K 棒的日期」。

```python
def assign_trading_day(timestamp: datetime, kbar_index: pd.DatetimeIndex) -> date:
    """
    依資料本身判定 trading_day（不依賴節假日表）。
    
    Args:
        timestamp: K 棒時間戳
        kbar_index: 整個 1 分 K 資料的 DatetimeIndex（用於查下一交易日）
    
    Returns:
        該 K 棒所屬的台指期交易日
    """
    t = timestamp.time()
    d = timestamp.date()
    
    # 日盤：08:45-13:45 → 當日
    if time(8, 45) <= t <= time(13, 45):
        return d
    
    # 夜盤前段：15:00-23:59 → 下一個有 K 棒的交易日
    elif time(15, 0) <= t <= time(23, 59, 59):
        return _find_next_trading_day(d, kbar_index)
    
    # 夜盤後段：00:00-05:00
    elif time(0, 0) <= t <= time(5, 0):
        # 如果當日 08:45 之後有 K 棒，歸當日
        if _has_day_session_data(d, kbar_index):
            return d
        # 否則歸下一個交易日（極少見：颱風假等）
        else:
            return _find_next_trading_day(d, kbar_index)
    
    # 非交易時段（05:00-08:45 / 13:45-15:00）
    else:
        return None  # 標記為無效時段


def _find_next_trading_day(after_date: date, kbar_index: pd.DatetimeIndex) -> date:
    """從資料中找出 after_date 之後第一個有日盤 K 棒的日期"""
    candidates = kbar_index[
        (kbar_index.date > after_date) &
        (kbar_index.time >= time(8, 45)) &
        (kbar_index.time <= time(13, 45))
    ]
    if len(candidates) == 0:
        raise ValueError(f"找不到 {after_date} 之後的交易日")
    return candidates[0].date()


def _has_day_session_data(d: date, kbar_index: pd.DatetimeIndex) -> bool:
    """檢查 d 當日 08:45-13:45 是否有 K 棒"""
    mask = (
        (kbar_index.date == d) &
        (kbar_index.time >= time(8, 45)) &
        (kbar_index.time <= time(13, 45))
    )
    return mask.any()
```

### 2.4 資料表 Schema

```sql
CREATE TABLE minute_kbar (
    timestamp     TIMESTAMP NOT NULL,
    trading_day   DATE NOT NULL,           -- ETL 計算後寫入
    open          REAL NOT NULL,
    high          REAL NOT NULL,
    low           REAL NOT NULL,
    close         REAL NOT NULL,
    -- 預留欄位（Phase 1 全部 NULL，Phase 4+ 啟用）
    volume        INTEGER,
    open_interest INTEGER,
    foreign_net   INTEGER,
    PRIMARY KEY (timestamp)
);
CREATE INDEX idx_trading_day ON minute_kbar (trading_day);
```

---

## 3. 策略時段分類（4 類）

### 3.1 完整定義表

| 代碼 | 中文 | 觀察/開倉時段 | 冷卻期（不開倉）| 強制平倉 | 跨段持倉 | 跨日持倉 |
|---|---|---|---|---|---|---|
| `daytrade_day` | 日盤當沖 | 08:45-13:25 | **13:25-13:40** | **13:40 K open** | ❌ | ❌ |
| `daytrade_night` | 夜盤當沖 | 15:00-04:25 | **04:25-04:45** | **04:45 K open** | ❌ | ❌ |
| `swing_day` | 日盤波段 | 08:45-13:45 | 無 | 無 | ❌ 不看夜盤 | ✅ |
| `swing_full` | 全日盤波段 | 0845-1345 + 1500-0500 | 無 | 無 | ✅ | ✅ |

**已移除**：~~全日當沖（V1.3 原有）~~

### 3.2 各類詳細規則

#### 3.2.1 `daytrade_day` 日盤當沖

```yaml
trading_session: daytrade_day
rules:
  open_window:        "08:45 - 13:25"       # 不含 13:25 後
  cooldown_window:    "13:25 - 13:40"       # 不開倉，但仍可平倉
  force_close:        "13:40 K open"        # 用下一根 K 開盤價平倉
  cross_session:      forbidden              # 不可持倉到夜盤
  cross_day:          forbidden              # 不可持倉到隔日
  data_view:          "day_session_only"    # 策略只看日盤資料
```

#### 3.2.2 `daytrade_night` 夜盤當沖

```yaml
trading_session: daytrade_night
rules:
  open_window:        "15:00 - 04:25"       # 跨日，從 T-1 15:00 到 T 04:25
  cooldown_window:    "04:25 - 04:45"
  force_close:        "04:45 K open"
  cross_session:      forbidden              # 不可持倉到日盤
  cross_day:          forbidden              # 不可持倉超過一個夜盤
  data_view:          "night_session_only"
```

#### 3.2.3 `swing_day` 日盤波段

```yaml
trading_session: swing_day
rules:
  open_window:        "08:45 - 13:45"
  cooldown_window:    none
  force_close:        none
  cross_session:      forbidden              # 不看夜盤資料
  cross_day:          allowed                # 可持倉多日
  data_view:          "day_session_only"
  overnight_handling: "treat as gap"         # 隔夜跳動視為跳空缺口
```

**重要**：`swing_day` 持倉過夜時，不會在夜盤觸發停損（看不見夜盤資料）。但隔日開盤若大幅跳空，可能直接觸發停損。

#### 3.2.4 `swing_full` 全日盤波段

```yaml
trading_session: swing_full
rules:
  open_window:        "08:45-13:45 + 15:00-05:00"
  cooldown_window:    none
  force_close:        none
  cross_session:      allowed                # 1345-1500 段間休息可跨
  cross_day:          allowed
  data_view:          "all_sessions"
```

### 3.3 強制平倉設計理由

- **用「下一根 K 棒 open」平倉**：避免前視偏差（look-ahead bias）
- **設計範例**：
  ```
  13:39 K 收盤 → 判斷「無出場訊號 + 仍持倉」
  13:40 K open → 以這個價格市價平倉
  ```
- **訊號判斷與成交價分離**：符合實盤可執行性

### 3.4 冷卻期設計理由

收盤前 20 分鐘不開倉的考量：
1. 給已開倉部位有時間觸發停損/停利出場
2. 避免「最後一根進場、下一根強平」的無效交易
3. 符合實盤直覺（剩 20 分鐘進場勝率太低）
4. 適用於所有當沖類型（`daytrade_day`、`daytrade_night`）

---

## 4. 業務規則實作（核心類別）

### 4.1 時段分類器

```python
from enum import Enum
from datetime import time
from datetime import datetime
from typing import Literal


class SessionType(Enum):
    DAY = "day_session"               # 08:45-13:45
    NIGHT = "night_session"           # 15:00-05:00
    NON_TRADING = "non_trading"       # 其他


def classify_kbar_session(timestamp: datetime) -> SessionType:
    """根據 K 棒時間判定屬於哪個交易時段"""
    t = timestamp.time()
    
    if time(8, 45) <= t <= time(13, 45):
        return SessionType.DAY
    
    elif time(15, 0) <= t <= time(23, 59, 59):
        return SessionType.NIGHT
    
    elif time(0, 0) <= t <= time(5, 0):
        return SessionType.NIGHT
    
    else:
        return SessionType.NON_TRADING
```

### 4.2 開倉判定

```python
def can_open_position(kbar_time: time, trading_session: str) -> bool:
    """根據策略類型與當前時間，判定是否可開倉"""
    
    if trading_session == "daytrade_day":
        # 08:45-13:25 可開（13:25 後進入冷卻期）
        return time(8, 45) <= kbar_time < time(13, 25)
    
    elif trading_session == "daytrade_night":
        # 跨日邏輯
        if time(15, 0) <= kbar_time <= time(23, 59, 59):
            return True
        elif kbar_time < time(4, 25):
            return True
        return False
    
    elif trading_session == "swing_day":
        # 整個日盤都可開
        return time(8, 45) <= kbar_time <= time(13, 45)
    
    elif trading_session == "swing_full":
        # 任何交易時段都可開
        sess = classify_kbar_session_by_time(kbar_time)
        return sess != SessionType.NON_TRADING
    
    return False
```

### 4.3 強制平倉判定

```python
def must_force_close(kbar_time: time, trading_session: str) -> bool:
    """是否必須在當前 K 強制平倉（下一根 K open 成交）"""
    
    if trading_session == "daytrade_day":
        # 13:39 收盤 → 13:40 K open 強平 → 訊號在 13:39 觸發
        return kbar_time == time(13, 39)
    
    elif trading_session == "daytrade_night":
        return kbar_time == time(4, 44)
    
    return False  # swing_* 類無強平
```

### 4.4 swing_day 隔夜跳空處理

```python
def prepare_swing_day_data(df: pd.DataFrame) -> pd.DataFrame:
    """
    swing_day 策略只看日盤資料，夜盤跳動視為跳空缺口。
    
    實作方式：直接過濾資料只保留日盤 K 棒。
    backtrader / vectorbt 會自動把不連續視為 gap。
    """
    daytime_mask = (
        (df.index.time >= time(8, 45)) &
        (df.index.time <= time(13, 45))
    )
    return df[daytime_mask]
```

---

## 5. 合約與資料

### 5.1 合約轉倉

> **本系統不處理轉倉**。

- 使用者提供的 CSV 已是預先處理好的「**連續月**」資料
- ETL 視為單一連續商品，不需合約對照表
- 結算日不做特殊處理

### 5.2 資料時間範圍

- **2014-01-01 ~ 2025-12-31**（12 年）
- 約 130-260 萬筆 1 分 K（依交易日數變動）

### 5.3 資料品質假設

- 已預處理、無需清洗
- 連續月切換無斷裂或跳空
- 時區為 UTC+8（Asia/Taipei）

### 5.4 dev_data_range 加速開發機制

開發階段（Phase 1-2）為加速 backtrader WFA 跑測，可切換子集資料：

```yaml
# config/backtest.yaml
backtest_engine:
  full_data_range: "2014-01-01:2025-12-31"
  dev_data_range: "2022-01-01:2025-12-31"   # 4 年子集
  use_dev_range: true     # 開發時 true，生產時 false
```

**警告**：`use_dev_range: true` 時，WFA 結果不具統計意義（窗口太少），僅供開發 debug 用。**Phase 3 端到端整合測試前必須切回 false**。

---

## 6. KPI 篩選門檻

### 6.1 vectorbt 快篩門檻（V1.3 表 2.3）

```yaml
# config/backtest.yaml
python_filter:
  sharpe_min: 1.2
  max_drawdown_max: 0.35      # 最大回撤 ≤ 35%
  profit_factor_min: 1.3
  min_trades: 80              # 12 年至少 80 筆
  
  # 任一未達 → 直接淘汰，不進 WFA
```

### 6.2 backtrader WFA 合格條件

```yaml
wfa:
  is_months: 18
  oos_months: 6
  step_months: 6
  
  overfitting_threshold: 0.6   # 所有 OOS Sharpe 均值 / IS Sharpe 均值
                                # < 0.6 → overfitting_flag = True（不淘汰，標記）
```

### 6.3 進入 strategies_developed 的條件

- vectorbt 快篩通過
- WFA 跑完（不論是否 overfitting）
- EL 三層驗證通過、可產 PLA

`overfitting_flag = True` 的策略**仍進**入 Collection，但會在 Web UI 標示為「待人工複審」。

---

## 7. 失敗策略處理

### 7.1 失敗類型分類

```python
class FailureReason(Enum):
    EL_SYNTAX = "el_syntax_error"               # 三層驗證未過
    MC_COMPILE = "mc_compile_error"             # MC 編譯失敗
    INSUFFICIENT_TRADES = "insufficient_trades" # 交易筆數 < 80
    LOW_SHARPE = "low_sharpe"                   # Sharpe < 1.2
    HIGH_DRAWDOWN = "high_drawdown"             # MaxDD > 35%
    LOW_PROFIT_FACTOR = "low_profit_factor"     # PF < 1.3
    BUDGET_EXHAUSTED = "budget_exhausted"
    LLM_TIMEOUT = "llm_timeout"
    UNKNOWN = "unknown"
```

### 7.2 失敗策略寫入

```sql
CREATE TABLE failure_log (
    id              INTEGER PRIMARY KEY,
    strategy_name   TEXT NOT NULL,
    failure_reason  TEXT NOT NULL,
    failure_stage   TEXT NOT NULL,    -- prompt_step_1..5 / vectorbt / backtrader / mc_compile
    details         TEXT,              -- JSON
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 7.3 失敗反例向量化

> 失敗策略的「what_was_tried + why_failed」**也要嵌入** `strategies_failed` Collection，下次 RAG 檢索時當反例餵 LLM。

詳見 `docs/rag_design.md` 第 4 節。

---

## 8. EasyLanguage 業務規則對照

LLM 生成 EL 時，必須對應上述業務規則。以下是常見對照：

### 8.1 強平時點 EL 表達

```easylanguage
// daytrade_day 在 13:40 強平（用下一根 K open）
// 訊號判斷在 13:39 收盤
If Time = 1339 And MarketPosition <> 0 Then
    Sell Next Bar at Market;        // 多單平倉
    BuyToCover Next Bar at Market;  // 空單平倉
```

### 8.2 開倉時段限制

```easylanguage
// daytrade_day 只在 08:45-13:25 可開倉
Variable: bool canOpen(false);
canOpen = (Time >= 0845 and Time < 1325);

If canOpen and (進場條件) Then
    Buy Next Bar at Market;
```

### 8.3 滑價設定

> 在 EL 中不需特別處理滑價（MultiCharts 在 Strategy Properties 中設定）。但 vectorbt / backtrader 必須在 Python 層套用 4 點滑價。

### 8.4 EntriesToday 防當日重複進場

```easylanguage
// 從舊專案 TXDTA505 學到的標準寫法
If EntriesToday(Date) = 0 and (進場條件) Then
    Buy Next Bar at Market;
```

---

## 9. 回測引擎業務規則整合

### 9.1 vectorbt 整合點

```python
def apply_business_rules_vectorbt(
    df: pd.DataFrame,
    strategy_yaml: dict,
    raw_signals: np.ndarray
) -> np.ndarray:
    """
    將原始訊號套用業務規則：
    1. 過濾冷卻期內的進場訊號
    2. 在強平時點注入平倉訊號
    3. 套用 4 點滑價（在 Portfolio 計算時）
    """
    trading_session = strategy_yaml["trading_session"]
    
    # 1. 過濾冷卻期
    open_mask = vectorize(can_open_position)(df.index.time, trading_session)
    filtered_signals = raw_signals * open_mask
    
    # 2. 強平注入
    if trading_session in ("daytrade_day", "daytrade_night"):
        force_close_mask = vectorize(must_force_close)(df.index.time, trading_session)
        filtered_signals[force_close_mask] = -1  # 平倉訊號
    
    return filtered_signals
```

### 9.2 backtrader 整合點

```python
class BusinessRulesStrategy(bt.Strategy):
    """所有 backtrader 策略應繼承此基類"""
    
    params = (("trading_session", "daytrade_day"),)
    
    def next(self):
        current_time = self.data.datetime.time(0)
        
        # 強平判定
        if must_force_close(current_time, self.p.trading_session):
            if self.position:
                self.close()  # 下一根 K open 平倉
            return
        
        # 開倉前檢查冷卻期
        if not can_open_position(current_time, self.p.trading_session):
            return
        
        # 子類覆寫的進場邏輯
        self.check_entry_signals()
```

---

## 10. Phase 1 Few-shot EL 範例的對應規則

每個 Phase 1 手寫的 5-10 個 Few-shot EL 範例**必須包含**：

1. **時段判定**：在 EL 中用 `Time` 變數限制開倉時段
2. **冷卻期遵守**：13:25 / 04:25 後不開倉
3. **強平機制**：13:39 / 04:44 觸發 `Sell Next Bar at Market`
4. **停損條件**：必須有明確停損（防止策略無風控）
5. **EntriesToday 防重複**：避免當日多次進場
6. **註解清楚**：用中文註解策略邏輯

詳細 Few-shot 設計指引見 `docs/llm_prompts.md` 第 6 節。

---

**END OF business_rules.md**
