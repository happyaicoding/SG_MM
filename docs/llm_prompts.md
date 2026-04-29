# LLM Prompt 設計與 Few-shot 指引

> **文件用途**：5 段 prompt 串接設計、各段範本、Few-shot 範例庫設計指引
> **語言**：中文
> **適用對象**：Claude Code 進入 Phase 3 必讀；使用者手寫 Few-shot 前必讀

---

## 1. 核心設計原則

### 1.1 為什麼是「5 段串接」而不是「一段大 prompt」

> 這是直接針對舊專案「LLM 規劃不好導致策略品質差」的核心修正。

**舊專案的錯誤做法**（不可重蹈覆轍）：
```
單一 132 行 prompt：
"你是台指期策略專家，請根據以下需求生成 EL 程式碼，
 同時要考慮市場狀況、風控、停損停利、時段限制、
 並對齊我們的 RAG 知識庫，並做自我審核確認..."

問題：
  ❌ LLM 注意力分散
  ❌ 每件事都做不好
  ❌ 失敗時不知道哪個環節錯
  ❌ 無法針對單一環節 retry
```

**V1.4 正確做法**：5 個獨立 prompt 串接，各司其職

```
Step ① 意圖解讀     → 結構化 JSON
Step ② RAG 檢索     → DuckDB 查詢（不用 LLM）
Step ③ 策略骨架     → YAML 偽代碼
Step ④ EL 生成      → EasyLanguage 程式碼
Step ⑤ 自我審核     → JSON 評估結果
```

### 1.2 強制紀律

- **禁止**：把任意兩段合併成一個 prompt
- **禁止**：跳過任一段（即使覺得簡單）
- **強制**：Step ① 與 Step ⑤ 必須用 API 的 `tool_use` 強制 JSON
- **強制**：每段失敗必須記錄到 `llm_calls` 表

### 1.3 LLM 順序

每段 prompt 都依儲備遞進順序嘗試：**NIM → Minimax → Claude**。

- 一段成功 → 進入下一段
- 一段失敗（含格式錯誤、超時、內容明顯不對）→ 換下一個 LLM 重試該段
- 三家都失敗 → 該策略整體失敗，寫入 `failure_log`

---

## 2. Step ① 意圖解讀（Intent Parser）

### 2.1 任務

從使用者一句話 → 萃取結構化參數。

### 2.2 輸入範例

> 「我想做一個夜盤的均值回歸策略，用 5 分 K，停損抓 30 點」

### 2.3 必須產出（強制 JSON via tool_use）

```json
{
  "trading_session": "daytrade_night",
  "logic_type": "mean_reversion",
  "timeframe": "5min",
  "stop_loss_points": 30,
  "take_profit_points": null,
  "additional_constraints": [],
  "ambiguous_fields": [],
  "confidence": 0.92
}
```

### 2.4 Prompt 模板（`config/prompts/step1_intent.md`）

```markdown
# 角色
你是台指期策略需求解讀專家。將使用者的自然語言需求 → 結構化 JSON。

# 規則
1. trading_session 只能是：daytrade_day, daytrade_night, swing_day, swing_full
2. logic_type 只能是：trend, mean_reversion, tunnel, pattern
   - 注意：chip 與 reference 在 Phase 1 停用，遇到時返回 ambiguous
3. timeframe 只能是：1min, 5min, 15min, 30min, 60min, daily
4. 不確定的欄位放入 ambiguous_fields，不要猜
5. confidence < 0.7 時也要照常返回，由系統決定要不要問使用者

# 使用者輸入
{user_prompt}

# 必須以 JSON 工具呼叫返回
```

### 2.5 失敗處理

- LLM 不返回 JSON / 欄位不齊 → 視為該 LLM 失敗，換下一家
- `confidence < 0.7` → 返回前端 UI，跳出對話框問使用者澄清

---

## 3. Step ② RAG 檢索（不用 LLM）

> 這一步**不呼叫 LLM**，純粹 DuckDB 查詢。詳見 `docs/rag_design.md`。

### 3.1 流程

```
意圖 JSON
   ↓
Step 2.1：SQL 過濾結構化欄位
   WHERE trading_session = 'daytrade_night' AND logic_type = 'mean_reversion'
   ↓
Step 2.2：metadata_vector 粗篩 → top-20
   ↓
Step 2.3：semantic_vector 精篩 → top-5
   ↓
Step 2.4：多樣性過濾 → 排除餘弦相似度 > 0.85 的重複
   ↓
Step 2.5：撈失敗反例 → strategies_failed top-2（語意接近的失敗）
   ↓
返回給 Step ③：
{
  "positive_examples": [3 個成功策略],
  "negative_examples": [2 個失敗反例]
}
```

### 3.2 失敗處理

- 找不到 positive_examples → 用 `library/` 的 Few-shot 補（Phase 1 必須有 5-10 個 fallback）
- 找不到 negative_examples → 不影響流程（早期專案 strategies_failed 是空的）

---

## 4. Step ③ 策略骨架（Strategy Skeleton）

### 4.1 任務

把意圖 JSON + RAG 結果 → **中文 YAML 偽代碼**，方便人類審核策略邏輯。

### 4.2 為什麼先產 YAML 偽代碼，不直接生 EL

- **人類可讀**：使用者可以審核策略邏輯
- **語意層 vs 程式層分離**：先確認設計對，再寫程式
- **可重用性**：YAML 可給 vectorbt 直接跑快篩，不必等 EL

### 4.3 必須產出（純文字 YAML）

```yaml
strategy:
  name: "TXN_MeanRev_BollingerSqueeze_001"
  trading_session: "daytrade_night"
  logic_type: "mean_reversion"
  timeframe: "5min"
  
  description: |
    夜盤均值回歸策略：使用布林通道 + RSI 雙指標。
    當價格觸及通道下軌且 RSI < 30 時做多，
    觸及上軌且 RSI > 70 時做空。
    依據 V1.4 業務規則：04:25 起進入冷卻期不開倉，
    04:45 K open 強平。
  
  entry_rules:
    long:
      - "Close 觸及 BollingerLower(20, 2.0)"
      - "RSI(14) < 30"
      - "當前在夜盤可開倉時段內（15:00-04:25）"
    short:
      - "Close 觸及 BollingerUpper(20, 2.0)"
      - "RSI(14) > 70"
      - "當前在夜盤可開倉時段內"
  
  exit_rules:
    stop_loss: "30 點"
    take_profit: "60 點"
    force_close: "04:44 K 收盤訊號 → 04:45 K open 平倉"
  
  parameters:
    bollinger_period: 20
    bollinger_std: 2.0
    rsi_period: 14
    stop_loss_points: 30
    take_profit_points: 60
  
  reference_ideas:
    - id: "strategies_developed/abc123"
      similarity: 0.82
    - id: "strategies_failed/xyz789"
      similarity: 0.71
      reason_referenced: "避免犯下類似錯誤"
  
  market_assumption: |
    假設夜盤多頭與空頭力道在均值附近震盪，
    極端值常常回歸均值（適合非趨勢環境）。
  
  known_risks:
    - "強趨勢環境會被連續打停損"
    - "夜盤流動性低於日盤，滑價可能更大"
```

### 4.4 Prompt 模板（`config/prompts/step3_skeleton.md`）

```markdown
# 角色
你是台指期策略設計師。根據需求與參考範例，產出 YAML 偽代碼。

# 強制要求
1. 必須對齊 V1.4 業務規則（時段、冷卻期、強平、滑價）
2. 必須有停損機制
3. description 用繁體中文，明確說明進場/出場/停損
4. 參考範例（positive）的設計可以借鑑，但不能完全複製
5. 參考反例（negative）的失敗原因要避免

# 意圖
{intent_json}

# 正面範例（前 3 個成功策略）
{positive_examples}

# 反面範例（前 2 個失敗策略 + 失敗原因）
{negative_examples}

# 業務規則摘要
{business_rules_brief}

# 輸出格式
純 YAML，不要 markdown 標題或解釋文字。
```

### 4.5 失敗處理

- 不是有效 YAML → 換下一家 LLM
- YAML 缺必要欄位（entry_rules, exit_rules, force_close）→ 換下一家

---

## 5. Step ④ EL 程式碼生成（最關鍵也最容易錯）

### 5.1 任務

把 YAML 骨架 → **EasyLanguage 程式碼**。

### 5.2 為什麼這段最危險

- LLM 訓練資料中 EL 極少（冷門語言）
- 語法陷阱多（`Buy Next Bar at Market` vs `Buy at Market`）
- 自由發揮容易爆語法錯誤

### 5.3 Prompt 設計關鍵

#### 5.3.1 強制範本

要求 LLM 按固定段落結構產出：

```
[區塊 1] Inputs（參數宣告）
[區塊 2] Vars（變數宣告）
[區塊 3] 進場邏輯
[區塊 4] 出場邏輯（停損 + 停利）
[區塊 5] 強平邏輯（含時段判定）
[區塊 6] 註解（中文，描述邏輯）
```

#### 5.3.2 Few-shot 範例

附上 2-3 個你手寫的 Few-shot 範例（從 `library/` 動態取）。

#### 5.3.3 禁用語法黑名單

```markdown
# 禁用關鍵字（Phase 1）
- Volume      # 成交量資料未提供
- Ticks       # 同上
- OpenInt     # 未平倉量
- TimeToMinutes  # 用 Time 變數即可
```

#### 5.3.4 已知失敗模式

從 `el_validation_log` 累積，反餵 prompt：

```markdown
# 過去常見錯誤（請避免）
1. 漏寫 EntriesToday(Date) = 0 → 同日重複進場
2. 用 Sell at Market 而非 Sell Next Bar at Market → 前視偏差
3. Buy/Sell 不對稱 → 多空策略失衡
4. 忘記強平判定 → daytrade 持倉到隔日
```

### 5.4 Prompt 模板（`config/prompts/step4_el.md`）

```markdown
# 角色
你是 MultiCharts EasyLanguage 程式設計師。將 YAML 策略 → EL 程式碼。

# 嚴格規則

## 必須包含
1. Inputs 區塊（參數）
2. Vars 區塊（變數宣告）
3. 進場邏輯（多空對稱）
4. 停損機制（必須）
5. 強平邏輯（如為 daytrade_*，必須含時段判定）
6. EntriesToday(Date) = 0 防同日重複進場
7. 全部用 Next Bar 成交（避免前視偏差）

## 禁用關鍵字
{forbidden_keywords}

## 過去常見錯誤（請避免）
{accumulated_failure_patterns}

## 編碼
純 EL 程式碼，不要 markdown 包裹。允許中文註解（會自動轉 cp950）。

# 輸入

## YAML 策略骨架
{yaml_skeleton}

## Few-shot 範例（已驗證可用）
{few_shot_examples}

## 業務規則對照
{business_rules_summary}

# 輸出
純 EasyLanguage 程式碼。
```

### 5.5 失敗處理 + 回流

```
EL 生成 → Step ⑤ 自我審核
   ↓
進入 EL 三層驗證（詳見 docs/el_validation.md）
   ↓
任一層失敗：
   ├─ 把錯誤訊息附在 prompt → 重生（最多 3 次）
   └─ 三次都失敗 → 換下一個 LLM
```

---

## 6. Step ⑤ 自我審核（Self-Critique）

### 6.1 任務

由 **同一個 LLM、不同 prompt** 對 Step ④ 的 EL 程式碼做審核。

### 6.2 為什麼用同一個 LLM

- **效能考量**：API 呼叫已經很慢，不要再切換
- **一致性**：同一個 LLM 風格一致，避免格式不一致問題
- **成本考量**：跨 LLM 自審 = 雙倍呼叫
- **僅限這段**：不是放棄第三方審核（第三方審核由「EL 三層驗證 Layer 3」承擔）

### 6.3 必須產出（強制 JSON via tool_use）

```json
{
  "syntax_confidence": 0.85,
  "logic_match": true,
  "has_force_close": true,
  "has_stop_loss": true,
  "uses_forbidden_keywords": [],
  "issues": [
    {
      "line": 18,
      "severity": "warning",
      "message": "強平判定的時間值寫法可能不正確"
    }
  ],
  "suggested_fixes": [
    "第 18 行應改為 'If Time = 1339 Then'"
  ],
  "overall_quality": "good"
}
```

### 6.4 Prompt 模板（`config/prompts/step5_critique.md`)

```markdown
# 角色
你是 EasyLanguage 程式碼審核員。檢查程式碼是否符合規範，並給出信心評估。

# 檢查項目（依序逐項）
1. 語法是否正確？
2. 邏輯是否符合提供的 YAML 描述？
3. 是否有強平機制（如為 daytrade_*）？
4. 是否有停損機制（必須）？
5. 是否使用了禁用關鍵字？
6. EntriesToday(Date) = 0 是否存在（防重複進場）？
7. 是否全部用 Next Bar 成交？

# 評分
- syntax_confidence：0.0-1.0
- 信心 < 0.7 → 標記為「需修正」

# YAML 骨架
{yaml_skeleton}

# 待審核 EL 程式碼
{el_code}

# 必須以 JSON 工具呼叫返回
```

### 6.5 失敗處理

- 信心 < 0.7 → 把 issues 附在 prompt，重生 EL（回到 Step ④）
- LLM 不返回 JSON → 換下一家 LLM 從 Step ① 重新跑（少見）

---

## 7. Few-shot 範例庫設計指引（Phase 3 Week 3 必讀）

> 這是你（使用者）手寫 5-10 個 Few-shot 策略時的指引。

### 7.1 覆蓋矩陣（建議）

> 你可以選 5、7 或 10 個。我建議至少做 7 個（覆蓋 4 種 trading_session × 主要 logic_type）。

| # | trading_session | logic_type | 必要性 |
|---|---|---|---|
| 1 | daytrade_day | trend | ⭐⭐⭐ 必要 |
| 2 | daytrade_day | mean_reversion | ⭐⭐⭐ 必要 |
| 3 | daytrade_day | tunnel | ⭐⭐ 高 |
| 4 | daytrade_night | trend | ⭐⭐⭐ 必要 |
| 5 | daytrade_night | mean_reversion | ⭐⭐ 高 |
| 6 | swing_day | trend | ⭐⭐⭐ 必要 |
| 7 | swing_day | pattern | ⭐⭐ 高 |
| 8 | swing_full | trend | ⭐ 中 |
| 9 | swing_full | tunnel | ⭐ 中 |
| 10 | daytrade_day | pattern | ⭐ 中 |

**舊專案的 TXDTA505 直接成為 #1 或 #2**（已驗證的成熟範例）。

### 7.2 EL 程式碼結構模板

每個 Few-shot 範例必須包含：

```easylanguage
//================================================
// 策略名稱：[必填]
// 類型：daytrade_day | trend
// 時間框架：1 分 K
// 設計理由：[2-3 句中文說明]
//================================================

// === [區塊 1] Inputs（參數）===
Inputs:
    FastLength(5),
    SlowLength(20),
    StopLossPoints(30);

// === [區塊 2] Vars（變數）===
Variables:
    bool canOpen(false),
    double fastMA(0),
    double slowMA(0);

// === [區塊 3] 計算指標 ===
fastMA = Average(Close, FastLength);
slowMA = Average(Close, SlowLength);

// === [區塊 4] 開倉時段判定 ===
canOpen = (Time >= 0845 and Time < 1325);  // daytrade_day 規則

// === [區塊 5] 進場邏輯（多空對稱）===
If canOpen And EntriesToday(Date) = 0 Then Begin
    // 多單：快線上穿慢線
    If fastMA crosses over slowMA Then
        Buy Next Bar at Market;
    
    // 空單：快線下穿慢線
    If fastMA crosses under slowMA Then
        Sell Short Next Bar at Market;
End;

// === [區塊 6] 停損 ===
SetStopLoss(StopLossPoints * BigPointValue);

// === [區塊 7] 強平（13:39 收盤判定 → 13:40 K open 平倉）===
If Time = 1339 Then Begin
    If MarketPosition = 1 Then
        Sell Next Bar at Market;
    If MarketPosition = -1 Then
        BuyToCover Next Bar at Market;
End;
```

### 7.3 對應 YAML metadata（必附）

每個 EL 範例必須附一份對應 YAML：

```yaml
strategy:
  name: "TXDay_MA_Cross_001"
  trading_session: "daytrade_day"
  logic_type: "trend"
  timeframe: "1min"
  direction: "both"
  
  description: |
    日盤當沖 MA 交叉策略：快慢均線交叉作為進出場訊號。
    08:45-13:25 可開倉；13:25-13:40 冷卻期；13:40 K open 強平。
  
  why_it_works: |
    日盤趨勢段往往持續 30-60 分鐘，MA 交叉能捕捉中段趨勢。
    冷卻期確保有足夠時間出場，避免最後一筆無效。
  
  entry_long: "fastMA crosses over slowMA"
  entry_short: "fastMA crosses under slowMA"
  exit: "停損 30 點 / 13:40 強平"
  
  parameters:
    FastLength: 5
    SlowLength: 20
    StopLossPoints: 30
  
  market_assumption: "趨勢延續環境"
  known_weaknesses: "震盪盤會被連續打停損"
```

### 7.4 品質檢查清單（自我審核用）

寫完每個範例，自我檢查：

- [ ] 必要區塊全有：Inputs / Vars / 進場 / 停損 / 強平 / 註解
- [ ] 多空對稱（除非設計上有理由不對稱）
- [ ] 用 Next Bar 成交（避免前視偏差）
- [ ] 有 `EntriesToday(Date) = 0` 防同日重複
- [ ] 時段判定正確（與 trading_session 對應）
- [ ] 強平時點正確（13:39 / 04:44）
- [ ] 中文註解清楚
- [ ] 變數命名一致
- [ ] 對應 YAML metadata 完整

### 7.5 常見 EL 寫法陷阱（避免犯）

| 錯誤 | 正確 | 說明 |
|---|---|---|
| `Buy at Market` | `Buy Next Bar at Market` | 前視偏差 |
| 沒有 `EntriesToday(Date) = 0` | 加上 | 同日重複進場 |
| 用 `If Time = 13:40` | 用 `If Time = 1340` | EL 時間是整數 |
| 用 `Volume > X` | 不用 | Phase 1 不開放 |
| 變數名與 EL 關鍵字衝突（如 `time`、`high`）| 用 `myTime`、`barHigh` | 大小寫不敏感 |

### 7.6 Phase 3 Few-shot 工作流

```
Phase 3 Week 3 開始：

1. Claude Code 先產生 docs/few_shot_design_guide.md（細部）
2. 你讀完指引（30 分鐘）
3. 你手寫 5-10 個 Few-shot（5 小時）
   - 推薦從 TXDTA505 起步（已有）
   - 之後寫 6 個新的（每個約 30-40 分鐘）
4. Claude Code 跑語法檢查 + MC 試編譯（每個範例都要過）
5. 入庫 library/ + 嵌入 strategies_developed
6. 之後 Step ④ EL 生成時自動取用
```

---

## 8. Prompt 版本管理

### 8.1 為什麼要版本管理

LLM Prompt 是**會持續迭代的核心資產**。
- Phase 1-3 會快速迭代
- 不同版本的 prompt 對應不同的失敗模式累積
- 必須能 rollback

### 8.2 目錄結構

```
config/prompts/
├── current/                  # symbolic link → v3/
├── v1/
│   ├── step1_intent.md
│   ├── step3_skeleton.md
│   ├── step4_el.md
│   └── step5_critique.md
├── v2/
│   └── ...
├── v3/
│   └── ...
└── CHANGELOG.md              # 每版本的迭代理由
```

### 8.3 版本切換

```python
# src/core/ai_engine/prompt_steps/base.py
def load_prompt(step_name: str) -> str:
    version = os.getenv("PROMPT_VERSION", "current")
    path = f"config/prompts/{version}/{step_name}.md"
    return Path(path).read_text(encoding="utf-8")
```

---

## 9. 與品質保證機制的對應

| Step | 對應品質機制（詳見 quality_safeguards.md） |
|---|---|
| Step ② RAG 檢索 | #1 多向量分層、#2 分層檢索、#3 失敗反例、#5 多樣性 |
| Step ① / ⑤ JSON 強制 | #6 5 段 prompt 串接（避免退化）|
| Step ④ EL 生成 + Layer 3 編譯 | #8 EL 三層驗證 |
| 整體 LLM 編排 | #7 儲備遞進 + 預算降級、#10 成本-品質追蹤 |

---

## 10. 開發注意事項

### 10.1 Prompt 修改紀律

- 修改 prompt **必須** 同時提交版本號（v1 → v2）
- **必須**寫進 `config/prompts/CHANGELOG.md`
- 不可以「悄悄改 prompt」（會破壞品質追蹤的可比較性）

### 10.2 Token 估算

每段 prompt 的 token 預算（粗估）：

| Step | 輸入 tokens | 輸出 tokens | 備註 |
|---|---|---|---|
| ① 意圖解讀 | ~500 | ~200 | 強制 JSON |
| ② RAG | 0 | 0 | 不用 LLM |
| ③ 策略骨架 | ~3000 | ~1500 | 含 RAG 結果 |
| ④ EL 生成 | ~5000 | ~2000 | 含 Few-shot |
| ⑤ 自我審核 | ~2500 | ~500 | 強制 JSON |
| **合計** | **~11000** | **~4200** | 單次成功流程 |

### 10.3 失敗 retry 的 token 倍增

```
最壞情況：
  NIM 失敗（11k 進 + 4k 出）+
  Minimax 失敗（11k 進 + 4k 出）+
  Claude 成功（11k 進 + 4k 出）+
  Layer 3 編譯失敗，附錯誤訊息回流（額外 5k 進 + 2k 出）×3 次

→ 約 78k 進 + 27k 出
→ 配合預算控制，避免單一策略燒爆
```

---

**END OF llm_prompts.md**
