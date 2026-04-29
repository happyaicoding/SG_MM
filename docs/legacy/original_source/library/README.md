# AISMART 策略參考庫 (Strategy Library)

放置**已上線實戰**的 MultiCharts 策略，作為 LLM 生成新策略時的高品質參考範例。

## 為什麼需要這個資料夾？

- `confirmed/` 是「自動跑過 Python 初篩 + MTC 人工驗證通過」的策略 — 中等信任度
- `library/` 是「**你親自實盤過、有真實獲利紀錄**」的策略 — 高信任度
- LLM 生成新策略時優先參考 `library/`，學習其中的：
  - 風控結構（止損、獲利了結、移動停利）
  - 時段過濾（日盤/夜盤、收盤前平倉）
  - 進場濾網（避免假突破、震盪盤過濾）
  - 反向訊號處理（冷卻期、訊號確認）

## 兩層分類設計

策略用兩個正交維度分類：

| 維度 | 名稱 | 取值 | 用途 |
|------|------|------|------|
| **大分類** | `holding_type` | `daytrade` / `swing` | 持倉週期；決定時段風控設計 |
| **小分類** | `category` | `trend` / `mean_reversion` / `opening` / `scalp` / `swing` / `pattern` | 策略邏輯類型 |

注意：小分類的 `swing`（日內波段操作風格）與大分類的 `swing`（跨日持有）意義不同 — 靠 `holding_type` 區分。例：
- `holding_type=daytrade, category=swing` = 日內波段風格
- `holding_type=swing, category=trend` = 多日趨勢追隨

## 資料夾結構

**大分類為資料夾**（直觀好瀏覽），小分類寫在 yaml 裡：

```
library/
├─ daytrade/                  ← 當沖策略（當日進場、當日平倉）
│   ├─ TXDTA505.ELS           ← MultiCharts ELS 純文字
│   ├─ TXDTA505.yaml          ← 元資料（含 category）
│   ├─ MR_BB_5m.ELS
│   ├─ MR_BB_5m.yaml
│   └─ ...
└─ swing/                     ← 波段策略（跨日持有）
    ├─ Trend_Daily.ELS
    ├─ Trend_Daily.yaml
    └─ ...
```

## 如何加入策略

### 步驟 1：從 MultiCharts 匯出 ELS 純文字
PLEditor → 開啟策略 → File → **Export Strategy** → 選 `.ELS` 格式 → 存到對應 holding_type 資料夾。

> ⚠️ `.PLA` 是二進位編譯檔，LLM 看不懂；必須匯出 `.ELS` 純文字。

### 步驟 2：撰寫 yaml 元資料
與 .ELS 同名（副檔名 .yaml），範例：

```yaml
name: TXDTA505                # 必填：策略英文名
holding_type: daytrade        # 必填：大分類 daytrade / swing
category: trend               # 必填：小分類 6 類之一
timeframe: 5min               # 必填：1min / 5min / 15min / 30min / 60min / 1D
direction: both               # 必填：both / long / short
description: |                # 必填：策略邏輯與意圖（中英文皆可，越具體越好）
  日盤當沖突破策略：
  以近三日收盤價最高/最低定義趨勢區間，K 棒收盤突破時順勢進場。
  進場條件：
    1. 收盤 > 昨收 + 0.8 × 趨勢區間
    2. (H+L)/2 > max(今開, 三日均)
    ...
  出場條件：
    1. 固定停損 50 點
    2. 拉回追蹤出場
    3. 13:35 強制平倉

risk_features:                # 推薦：列出風控特性，幫 LLM 學習
  - "EntriesToday <= 0：每日只進場一次"
  - "固定停損 + 30 根後減半"
  - "13:35 強制平倉"
proven_period: "2023-01 ~ 2024-12 實盤"   # 推薦
notes: |                                   # 選填
  策略命名 TXDTA505 = TX Day Trade A 505
  使用 EntriesToday 防止當日重複進場
```

### 步驟 3：重建向量索引
```cmd
python scripts/index_library.py             # 增量更新
python scripts/index_library.py --rebuild   # 全部重建
```

之後執行 `generate_and_compile.py` 時，LLM 會自動從 library 抽出語意相關的策略當參考：
```cmd
python scripts/generate_and_compile.py --type trend --holding daytrade
```

## 元資料欄位完整說明

| 欄位 | 必填 | 取值 | 說明 |
|------|------|------|------|
| `name` | ✅ | string | 策略英文名（與 .ELS 內邏輯有對應） |
| `holding_type` | ✅ | `daytrade` / `swing` | 大分類，必須與資料夾名一致（小寫） |
| `category` | ✅ | trend / mean_reversion / opening / scalp / swing / pattern | 小分類 |
| `timeframe` | ✅ | `1min` / `5min` / `15min` / `30min` / `60min` / `1D` | 適用 K 棒週期 |
| `direction` | ✅ | both / long / short | 交易方向 |
| `description` | ✅ | multi-line text | 策略邏輯（注意：sub-section 需縮排，否則 yaml 會解析為新 key） |
| `proven_period` | 推薦 | string | 實盤期間描述 |
| `risk_features` | 推薦 | list of strings | 風控特性列表 |
| `notes` | 選填 | multi-line text | 實戰心得、踩過的坑 |
