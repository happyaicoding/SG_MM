# EasyLanguage 三層驗證機制

> **文件用途**：EL 程式碼三層驗證的設計、實作、失敗回流機制
> **語言**：中文
> **適用對象**：Claude Code 在 Phase 3 必讀

---

## 1. 為什麼需要三層驗證

### 1.1 舊專案的問題

舊專案的 EL 驗證僅有第 1 層基本檢查（必要關鍵字、行數），**缺少**：
- LLM 自我審核（catch 邊界錯誤）
- MC 試編譯（catch 真實語法錯誤）
- 失敗模式累積（讓 LLM 自我學習）

結果：失敗策略卷進 MC 試跑才發現問題，浪費時間與 token。

### 1.2 V1.4 三層設計的目的

```
┌──────────────────────────────────────────────────┐
│  第 1 層：規則式靜態檢查（< 0.1 秒，免費）         │
│  攔截 80% 明顯錯誤                                │
└──────────────────────────────────────────────────┘
                ↓ 通過
┌──────────────────────────────────────────────────┐
│  第 2 層：LLM 自我審核（同 LLM 不同 prompt）       │
│  攔截邏輯不一致、邊界條件                          │
└──────────────────────────────────────────────────┘
                ↓ 通過
┌──────────────────────────────────────────────────┐
│  第 3 層：MC Bridge 試編譯（最權威）               │
│  攔截真實語法錯誤、平台限制                        │
│  失敗訊息回流給 LLM 自動修正（最多 3 次）          │
└──────────────────────────────────────────────────┘
```

---

## 2. 第 1 層：規則式靜態檢查

### 2.1 設計

純 Python 規則式檢查，不呼叫 LLM、不呼叫 MC。執行時間 < 0.1 秒。

### 2.2 檢查項目

| # | 規則 | 嚴重度 |
|---|---|---|
| R01 | 必須有 `Inputs:` 區塊 | 🔴 致命 |
| R02 | 必須有 `Vars:` 或 `Variables:` 區塊 | 🔴 致命 |
| R03 | 必須有 `Buy ... Next Bar at` 或同類進場指令 | 🔴 致命 |
| R04 | 必須有 `Sell Short` 或對應空單進場 | 🟠 高（除非單向策略） |
| R05 | 必須有 `SetStopLoss` 或同類停損機制 | 🔴 致命 |
| R06 | 必須有 `EntriesToday(Date) = 0` 或類似防重複 | 🟡 中 |
| R07 | `Begin/End` 配對（含巢狀） | 🔴 致命 |
| R08 | `If/Then` 結構完整 | 🔴 致命 |
| R09 | 不可使用禁用關鍵字 | 🔴 致命 |
| R10 | （daytrade_*）必須有強平判定（`Time = 1339` 或 `Time = 0444`） | 🔴 致命 |
| R11 | （daytrade_*）必須有開倉時段判定（`Time >=` 與 `Time <`） | 🟠 高 |
| R12 | 全部成交用 `Next Bar`（避免前視偏差） | 🟠 高 |

### 2.3 禁用關鍵字清單

```python
# config/el_validation.yaml
forbidden_keywords:
  phase_1:
    - Volume        # 成交量資料未提供
    - Ticks         # 同上
    - OpenInt       # 未平倉量
    - TimeToMinutes # 用 Time 整數即可
  
  global:
    # 永遠禁用（與 V1.4 規格衝突）
    - intrabarordergeneration
```

### 2.4 實作

```python
# src/core/el_validation/layer1_static.py

from dataclasses import dataclass
from typing import Literal
import re


@dataclass
class ValidationIssue:
    rule_id: str
    severity: Literal["fatal", "high", "medium", "low"]
    message: str
    line: int | None = None


@dataclass
class Layer1Result:
    passed: bool
    issues: list[ValidationIssue]
    
    @property
    def has_fatal(self) -> bool:
        return any(i.severity == "fatal" for i in self.issues)


def validate_el_layer1(
    el_code: str,
    trading_session: str,
    forbidden_keywords: list[str]
) -> Layer1Result:
    """
    第 1 層：規則式靜態檢查
    
    Args:
        el_code: 待檢查的 EL 程式碼
        trading_session: 策略時段類型（決定哪些規則必檢）
        forbidden_keywords: 禁用關鍵字清單
    
    Returns:
        Layer1Result：含通過/失敗 + issue 清單
    """
    issues = []
    
    # R01: Inputs 區塊
    if not re.search(r"\binputs?\s*:", el_code, re.IGNORECASE):
        issues.append(ValidationIssue("R01", "fatal", "缺少 Inputs 區塊"))
    
    # R02: Vars 區塊
    if not re.search(r"\b(vars?|variables?)\s*:", el_code, re.IGNORECASE):
        issues.append(ValidationIssue("R02", "fatal", "缺少 Vars/Variables 區塊"))
    
    # R03: Buy 進場
    if not re.search(r"\bbuy\b.*\bnext bar\b", el_code, re.IGNORECASE):
        issues.append(ValidationIssue("R03", "fatal", "缺少 Buy Next Bar 進場指令"))
    
    # R04: Sell Short
    if not re.search(r"\bsell\s+short\b.*\bnext bar\b", el_code, re.IGNORECASE):
        issues.append(ValidationIssue("R04", "high", "缺少 Sell Short 空單進場（單向策略可忽略）"))
    
    # R05: 停損
    if not re.search(r"\b(setstoploss|setpercenttrailing|setdollartrailing)\b", el_code, re.IGNORECASE):
        issues.append(ValidationIssue("R05", "fatal", "缺少停損機制"))
    
    # R06: EntriesToday 防重複進場
    if not re.search(r"\bentriestoday\s*\(.*\)\s*=\s*0", el_code, re.IGNORECASE):
        issues.append(ValidationIssue("R06", "medium", "建議加入 EntriesToday(Date) = 0 防重複進場"))
    
    # R07: Begin/End 配對
    begin_count = len(re.findall(r"\bbegin\b", el_code, re.IGNORECASE))
    end_count = len(re.findall(r"\bend\b", el_code, re.IGNORECASE))
    if begin_count != end_count:
        issues.append(ValidationIssue(
            "R07", "fatal",
            f"Begin/End 配對不平衡（Begin={begin_count}, End={end_count}）"
        ))
    
    # R08: If/Then 結構（簡化版）
    if_count = len(re.findall(r"\bif\b", el_code, re.IGNORECASE))
    then_count = len(re.findall(r"\bthen\b", el_code, re.IGNORECASE))
    if if_count > then_count:
        issues.append(ValidationIssue(
            "R08", "fatal",
            f"If/Then 不對稱（If={if_count}, Then={then_count}）"
        ))
    
    # R09: 禁用關鍵字
    for keyword in forbidden_keywords:
        # 使用 word boundary 避免誤判（例如 Volume 不該匹配到 InVolatile）
        if re.search(rf"\b{re.escape(keyword)}\b", el_code, re.IGNORECASE):
            issues.append(ValidationIssue(
                "R09", "fatal",
                f"使用禁用關鍵字: {keyword}"
            ))
    
    # R10/R11: daytrade_* 專屬規則
    if trading_session == "daytrade_day":
        if not re.search(r"\btime\s*=\s*1339\b", el_code, re.IGNORECASE):
            issues.append(ValidationIssue("R10", "fatal", "daytrade_day 缺少 13:39 強平判定"))
        if not re.search(r"\btime\s*>=\s*0?845\b", el_code, re.IGNORECASE):
            issues.append(ValidationIssue("R11", "high", "daytrade_day 缺少開倉時段下界判定"))
    
    elif trading_session == "daytrade_night":
        if not re.search(r"\btime\s*=\s*0?444\b", el_code, re.IGNORECASE):
            issues.append(ValidationIssue("R10", "fatal", "daytrade_night 缺少 04:44 強平判定"))
    
    # R12: 全部用 Next Bar 成交
    bare_buy = re.findall(r"\bbuy\s+\d*\s*shares?\s+at\s+market\b", el_code, re.IGNORECASE)
    if bare_buy and not all("next bar" in b.lower() for b in bare_buy):
        issues.append(ValidationIssue("R12", "high", "發現未用 Next Bar 的進場（可能前視偏差）"))
    
    # 結果
    has_fatal = any(i.severity == "fatal" for i in issues)
    return Layer1Result(passed=not has_fatal, issues=issues)
```

### 2.5 失敗處理

- 有 `fatal` 級別 → **不進入第 2 層**，直接寫入 `el_validation_log`，回流給 LLM 修正
- 只有 `high` / `medium` 級別 → 警告後繼續到第 2 層

---

## 3. 第 2 層：LLM 自我審核

### 3.1 設計

由 **同一個生成的 LLM** 用 **不同的 prompt（審核者角色）** 對 EL 做審核。

### 3.2 為什麼用同 LLM

詳見 `docs/llm_prompts.md` §6.2。簡言之：效能、一致性、成本考量。

### 3.3 必須產出（強制 JSON via tool_use）

```json
{
  "syntax_confidence": 0.85,
  "logic_match": true,
  "has_force_close": true,
  "has_stop_loss": true,
  "has_entries_today_guard": true,
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

### 3.4 Prompt 模板

詳見 `docs/llm_prompts.md` §6.4。

### 3.5 通過判定

```python
def layer2_passed(critique: dict) -> bool:
    return (
        critique["syntax_confidence"] >= 0.7 and
        critique["logic_match"] is True and
        critique["has_stop_loss"] is True and
        critique["has_force_close"] is True and  # daytrade_* 才檢查
        len(critique["uses_forbidden_keywords"]) == 0 and
        critique["overall_quality"] in ("good", "acceptable")
    )
```

### 3.6 失敗處理

- 任一項未通過 → 把 issues 與 suggested_fixes 附在 prompt，回到 Step ④ 重生 EL（最多 3 次）

---

## 4. 第 3 層：MC Bridge 試編譯

### 4.1 設計

透過 MC Bridge 微服務（裸跑於 Windows）呼叫 PLEditor.exe 編譯 EL → 取得真實編譯結果。

### 4.2 流程

```
EL 程式碼通過 Layer 1+2
   ↓
HTTP POST → mc_bridge:8001/api/compile
   ├─ Body: { el_code, strategy_name }
   ↓
[ MC Bridge 內部 ]
   1. 將 el_code 用 cp950 寫入 Studies/SrcEl/Strategies/AI_TEST.el
   2. PLEditor 已開（背景守衛確保視窗未崩潰）
   3. 對話框守衛背景執行緒就緒
   4. 送出 Ctrl+F7 鍵盤事件（Build All）
   5. 監控 PLEditor 編譯結果視窗（成功 / 錯誤訊息）
   6. 解析結果：
       - 成功 → 確認 DLL 已產出 → 回傳成功 + PLA 檔路徑
       - 失敗 → 解析錯誤訊息（行號、錯誤描述）→ 回傳結構化錯誤
   ↓
回應給呼叫端：
   {
     "status": "ok" | "compile_error" | "system_error",
     "pla_file_path": ...,
     "compile_time_ms": 1234,
     "errors": [{"line": 12, "message": "Unknown identifier 'Volum'"}]
   }
```

### 4.3 MC Bridge 內部實作（沿用舊專案 `mc_bridge.py` 1261 行）

舊專案已有完整實作，包含：

1. **EL 寫入**：`_clean_el_for_pleditor()` + cp950 編碼
2. **編譯觸發**：pywinauto 送 Ctrl+F7
3. **對話框守衛**：背景 thread 自動關閉「儲存？」「覆寫？」等對話框
4. **錯誤訊息解析**：從 PLEditor 的錯誤視窗抓取
5. **DLL 偵測**：檢查 `Dlls/Strategies/AI_TEST.dll` 是否更新

> Phase 3 直接 copy 進新專案 `src/core/mc_bridge/`，不修改。

### 4.4 編譯失敗的常見原因

| 錯誤類型 | 範例 | 處理方式 |
|---|---|---|
| 變數名拼錯 | `Volum` 而非 `Volume` | 回流給 LLM，附原訊息修正 |
| 函式參數錯誤 | `Average(Close, "20")` | 回流給 LLM |
| 自定義函式不存在 | `BollingerLower(...)` 未 import | 回流給 LLM 改用內建函式 |
| 語法陷阱 | 缺少 `;` | 回流給 LLM |
| 平台限制 | 嘗試使用 Volume（已禁用）| 不該到這層才發現（Layer 1 應攔截）|

### 4.5 失敗回流邏輯

```python
def retry_with_compile_error(
    strategy_id: int,
    el_code: str,
    compile_errors: list[dict],
    llm_provider: str,
    retry_count: int = 0
) -> str:
    """
    把編譯錯誤附在 prompt 給 LLM 修正。
    """
    if retry_count >= 3:
        raise MaxRetriesExceeded(strategy_id)
    
    error_summary = "\n".join(
        f"- 第 {e['line']} 行: {e['message']}"
        for e in compile_errors
    )
    
    fix_prompt = f"""
    以下 EL 程式碼編譯失敗，請修正錯誤後重新提供完整 EL：
    
    # 編譯錯誤
    {error_summary}
    
    # 原始 EL
    {el_code}
    
    # 修正後 EL（請提供完整版）
    """
    
    fixed_el = llm_call(llm_provider, fix_prompt)
    return fixed_el
```

---

## 5. 失敗模式累積（核心品質機制）

### 5.1 為什麼累積

> 舊專案重複犯錯：LLM 同一個錯誤模式（如「Volume vs Volum」）會重犯數十次，直到使用者人工介入。

V1.4 做法：累積失敗模式，反餵下一次 prompt（讓 LLM 在生成時就避開）。

### 5.2 資料表

```sql
CREATE TABLE el_validation_log (
    id INTEGER PRIMARY KEY,
    strategy_id INTEGER NOT NULL,
    layer INTEGER NOT NULL,            -- 1 | 2 | 3
    success BOOLEAN NOT NULL,
    error_pattern TEXT,                -- 規則化的錯誤模式
    error_message TEXT,                -- 原始錯誤訊息
    el_snippet TEXT,                   -- 出錯的 EL 片段
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 5.3 錯誤模式規則化

```python
def regularize_error(error_message: str) -> str:
    """
    把錯誤訊息規則化成可分類的模式。
    
    範例：
    "Unknown identifier 'Volum' at line 12"
    → "UNKNOWN_IDENTIFIER:Volum"
    
    "Mismatched 'Begin' at line 5"
    → "MISMATCHED_BEGIN_END"
    """
    if "Unknown identifier" in error_message:
        match = re.search(r"'(\w+)'", error_message)
        if match:
            return f"UNKNOWN_IDENTIFIER:{match.group(1)}"
    
    if "Mismatched" in error_message:
        return "MISMATCHED_BEGIN_END"
    
    if "Expected" in error_message:
        return "SYNTAX_EXPECTED"
    
    return "OTHER"
```

### 5.4 反餵機制

每月（或每 100 個失敗後）統計常見錯誤模式：

```python
def get_top_failure_patterns(limit: int = 5) -> list[dict]:
    """從 el_validation_log 撈出最常見錯誤模式"""
    sql = """
    SELECT error_pattern, COUNT(*) AS cnt
    FROM el_validation_log
    WHERE success = FALSE AND layer = 3
    GROUP BY error_pattern
    ORDER BY cnt DESC
    LIMIT ?
    """
    return db.fetch_all(sql, [limit])
```

寫進 Step ④ EL 生成的 prompt：

```markdown
# 過去常見錯誤（請避免）
1. UNKNOWN_IDENTIFIER:Volum (出現 23 次)
   → 應該寫 Volume，但 Phase 1 禁用此關鍵字
2. MISMATCHED_BEGIN_END (出現 18 次)
   → 確保每個 Begin 都有對應的 End
3. SYNTAX_EXPECTED (出現 15 次)
   → 注意行尾 `;`
```

### 5.5 自動更新 Few-shot

當某種錯誤模式累積到一定次數（如 50 次），自動觸發：
- 把「正確寫法」標記為 Few-shot 範例
- 下次生成優先使用正確寫法

---

## 6. 三層整合主流程

```python
# src/core/el_validation/__init__.py

from typing import Optional

class ELValidationPipeline:
    def __init__(self, llm_orchestrator, mc_bridge_client):
        self.llm = llm_orchestrator
        self.mc = mc_bridge_client
        self.max_retries = 3
    
    def validate(
        self,
        strategy_id: int,
        el_code: str,
        yaml_skeleton: dict,
        trading_session: str
    ) -> ValidationResult:
        """執行三層驗證，含失敗回流"""
        
        retry_count = 0
        current_el = el_code
        
        while retry_count <= self.max_retries:
            # Layer 1
            l1 = validate_el_layer1(
                current_el,
                trading_session,
                load_forbidden_keywords()
            )
            self._log(strategy_id, 1, l1.passed, l1)
            
            if not l1.passed:
                if retry_count >= self.max_retries:
                    return ValidationResult(passed=False, stage="layer1", final_el=current_el)
                # 回流修正
                current_el = self._retry_with_layer1_issues(
                    yaml_skeleton, current_el, l1.issues
                )
                retry_count += 1
                continue
            
            # Layer 2
            l2 = self._llm_critique(yaml_skeleton, current_el)
            self._log(strategy_id, 2, l2.passed, l2)
            
            if not l2.passed:
                if retry_count >= self.max_retries:
                    return ValidationResult(passed=False, stage="layer2", final_el=current_el)
                current_el = self._retry_with_layer2_issues(
                    yaml_skeleton, current_el, l2.issues
                )
                retry_count += 1
                continue
            
            # Layer 3：MC 試編譯
            l3 = self.mc.compile(current_el, strategy_name=f"AI_S{strategy_id}")
            self._log(strategy_id, 3, l3.success, l3)
            
            if l3.success:
                return ValidationResult(
                    passed=True,
                    final_el=current_el,
                    pla_file_path=l3.pla_file_path
                )
            
            if retry_count >= self.max_retries:
                return ValidationResult(passed=False, stage="layer3", final_el=current_el)
            
            # 回流修正
            current_el = self._retry_with_compile_errors(
                yaml_skeleton, current_el, l3.errors
            )
            retry_count += 1
        
        return ValidationResult(passed=False, stage="max_retries_exceeded")
    
    def _log(self, strategy_id, layer, success, result):
        """寫入 el_validation_log"""
        db.insert("el_validation_log", {
            "strategy_id": strategy_id,
            "layer": layer,
            "success": success,
            "error_pattern": self._extract_pattern(result),
            "error_message": self._format_errors(result),
            "el_snippet": self._extract_snippet(result),
        })
```

---

## 7. 與品質保證機制的對應

- **#3** 失敗反例向量化：失敗的策略也寫進 `strategies_failed`，下次 RAG 撈得到反例
- **#8** EL 三層驗證：本文件全部
- **#10** 成本-品質追蹤：每次 retry 記錄 LLM tokens / cost

---

## 8. Phase 3 實作里程碑

| Sub-milestone | 工時 | 驗收 |
|---|---|---|
| Layer 1 規則檢查實作 | 3h | 至少 12 條規則，每條有 unit test |
| Layer 2 LLM critique prompt + 工具呼叫 | 3h | 對 1 個正確 EL 與 1 個故意錯 EL 都能正確判斷 |
| Layer 3 MC Bridge 整合（沿用舊 `mc_bridge.py`） | 3h | 端到端：Python → MC 編譯成功 + 失敗 兩種情況 |
| 失敗回流邏輯 | 3h | 故意產錯誤 EL，確認 retry 3 次後正確修正 |
| `el_validation_log` 表 + 反餵 prompt | 2h | 累積 5 筆錯誤後 prompt 自動帶入 |

---

**END OF el_validation.md**
