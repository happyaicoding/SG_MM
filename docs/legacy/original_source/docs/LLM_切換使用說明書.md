# LLM 切換使用說明書

AISMART 支援多個 LLM 供應商，可透過設定檔或程式碼任意切換，無需修改策略邏輯。

---

## 目前支援的供應商

| Provider | 模型 | 說明 |
|----------|------|------|
| `claude` | claude-sonnet-4-20250514（預設） | Anthropic Claude，高品質生成 |
| `claude` | claude-haiku-4-5 | 輕量快速，適合批量測試 |
| `claude` | claude-opus-4-5 | 最高品質，成本較高 |
| `minimax` | MiniMax-M2.7（預設） | MiniMax，支援 204K context |
| `minimax` | MiniMax-M2.7-highspeed | 高速版（需較高帳號等級） |
| `minimax` | MiniMax-M2.5 / M2.1 / M2 | 舊版模型 |

---

## 方式一：修改 config.yaml（推薦，全域生效）

編輯專案根目錄的 `config.yaml`：

```yaml
# 切換供應商
llm:
  provider: claude      # 改為 minimax 即切換至 MiniMax

# Claude 模型設定
claude:
  model: claude-sonnet-4-20250514

# MiniMax 模型設定
minimax:
  model: MiniMax-M2.7
```

**切換步驟：**

1. 將 `llm.provider` 改為目標供應商
2. 確認對應的模型名稱正確
3. 存檔，重新啟動服務即生效（不需改任何程式碼）

---

## 方式二：程式碼指定（單次使用）

```python
from src.core.ai_engine.client import create_llm_client, ClaudeClient, MiniMaxClient
from src.core.ai_engine.generator import StrategyGenerator

# ── Claude ────────────────────────────────────────────────────────
# 從 config.yaml 讀取模型
gen = StrategyGenerator(client=create_llm_client("claude"))

# 指定 Claude 模型
gen = StrategyGenerator(client=create_llm_client("claude", model="claude-haiku-4-5"))

# ── MiniMax ───────────────────────────────────────────────────────
# 從 config.yaml 讀取模型（預設 MiniMax-M2.7）
gen = StrategyGenerator(client=create_llm_client("minimax"))

# 指定 MiniMax 模型
gen = StrategyGenerator(client=create_llm_client("minimax", model="MiniMax-M2.7-highspeed"))

# ── 直接建立客戶端（更底層的用法）────────────────────────────────
client = ClaudeClient(model="claude-opus-4-5")
client = MiniMaxClient(model="MiniMax-M2.7")
gen = StrategyGenerator(client=client)
```

---

## 方式三：自訂 LLM（接入其他供應商）

任何符合以下介面的類別都可以直接傳入，不需繼承任何基類：

```python
class MyCustomLLM:
    """自訂 LLM，只需實作兩個成員即可。"""

    @property
    def total_tokens(self) -> int:
        return self._tokens

    def chat(
        self,
        messages: list[dict],
        system: str = "",
        max_tokens: int = 2048,
        temperature: float = 0.7,
        tools: list[dict] | None = None,
    ) -> str:
        # 呼叫你的 LLM API
        return "策略程式碼..."

# 直接使用
from src.core.ai_engine.generator import StrategyGenerator
gen = StrategyGenerator(client=MyCustomLLM())
```

---

## 環境變數設定（.env 檔案）

專案根目錄建立 `.env`（參考 `.env.example`）：

```env
# Claude API Key（使用 Claude 時必填）
ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxxxxx

# MiniMax API Key（使用 MiniMax 時必填）
MINIMAX_API_KEY=your_minimax_api_key_here
```

> `.env` 已在 `.gitignore` 中，不會被 git 追蹤，請勿將真實 Key 提交至版本庫。

---

## 各供應商注意事項

### Claude
- API Key 申請：https://console.anthropic.com/
- temperature 範圍：`[0.0, 1.0]`
- 支援 `top_k`、`stop_sequences` 等進階參數

### MiniMax
- API Key 申請：https://platform.minimaxi.com/
- temperature 範圍：`(0.0, 1.0]`（**不含 0.0**，程式會自動夾緊至 0.01）
- M2.7 預設啟用 Thinking，max_tokens 自動提升至最低 1024
- **不支援**：圖片輸入、`top_k`、`stop_sequences`
- highspeed 模型需較高帳號等級

---

## 快速切換對照表

| 目的 | config.yaml 設定 |
|------|-----------------|
| 使用 Claude Sonnet（預設） | `llm.provider: claude`、`claude.model: claude-sonnet-4-20250514` |
| 使用 Claude Haiku（省成本） | `llm.provider: claude`、`claude.model: claude-haiku-4-5` |
| 使用 MiniMax M2.7 | `llm.provider: minimax`、`minimax.model: MiniMax-M2.7` |

---

## 驗證切換是否生效

```python
from src.core.ai_engine.client import create_llm_client

client = create_llm_client()    # 讀取 config.yaml 設定
print(type(client).__name__)    # ClaudeClient 或 MiniMaxClient
print(client.model)             # 確認模型名稱

reply = client.chat(
    messages=[{"role": "user", "content": "你是哪個 LLM？請用一句話回答。"}],
    max_tokens=1024,
)
print(reply)
```
