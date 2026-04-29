import urllib.request, json, subprocess

result = subprocess.run(
    ["git", "credential", "fill"],
    input="protocol=https\nhost=github.com\n\n",
    capture_output=True, text=True, cwd="C:/Users/admin/AISMART"
)
creds = dict(line.split("=", 1) for line in result.stdout.strip().split("\n") if "=" in line)
token = creds.get("password", "")

headers = {
    "Authorization": f"token {token}",
    "Accept": "application/vnd.github.v3+json",
    "Content-Type": "application/json",
}

new_title = "[Phase1+2] AISMART v1.1 — src/ refactor + AI engine (LLM multi-provider, Researcher, Optimizer, Reports)"

new_body = """## Summary

### Phase 1 — 基礎架構重構
- Restructure project to `src/` layout; migrate all modules
- `python_bt.py` + `metrics.py` + `wfa.py`: backtest engine + Walk-Forward Analysis
- `runner.py`: strategy runner with DB persistence
- `registry.py`: strategy auto-discovery

### Phase 2 — AI 引擎閉環（本 session 新增）
- **LLM 多供應商支援**：`BaseLLMClient` Protocol + `ClaudeClient` + `MiniMaxClient` + `create_llm_client()` factory
  - MiniMax M2.7 via Anthropic-compatible API; handles ThinkingBlock, temperature/max_tokens constraints
- **`StrategyGenerator`**：prompt → Claude/MiniMax → AST validation → 3-attempt self-correction → save to `src/strategies/generated/`
- **`Researcher`**：URL 知識庫（add/remove/list/research）；SQLite primary + JSON fallback；web_search tool for Claude only
- **`AIOptimizer`**：strategy_code + backtest_result → LLM → structured JSON suggestions (3-stage parse with fallback)
- **`URLKnowledge`** ORM table added to `models.py`
- **`html_report.py`**：Plotly interactive report — equity curve + monthly PnL heatmap + drawdown
- **`pdf_report.py`**：static HTML/PDF with filter threshold comparison table + monthly PnL grid
- **3 AI-generated strategies**: `MACD_MA_Trend`, `MACD_TrendConfirm`, `Trend_Catcher`
- **`docs/LLM_切換使用說明書.md`**: LLM switching guide
- **`CLAUDE.md`** complete rewrite for v1.1

### Refactor / Cleanup (/simplify)
- Shared `_report_utils.py` (`sanitize_filename`, `calc_monthly_pnl`) to eliminate duplication between reports
- Removed dead imports; fixed TYPE_CHECKING inconsistency in Researcher
- Fixed efficiency: single JSON load in `add_url()`, `_count_urls()` for O(1) log count
- Fixed `trend_catcher.py` bug: `macd_hist` was reading signal line (`MACDs_`) instead of histogram (`MACDh_`)
- `pivot.reindex(columns=months)` replaces 12-iteration column-fill loop

## Test plan

- [ ] Import test: `from src.core.ai_engine.client import create_llm_client, ClaudeClient, MiniMaxClient`
- [ ] Researcher JSON fallback: `r=Researcher(); r.add_url('https://example.com'); print(r.list_urls())`
- [ ] Optimizer parse: `AIOptimizer._parse_result('{"analysis":"ok","suggestions":[]}', 0).parse_ok == True`
- [ ] Report generation: `generate_html` and `generate_pdf` with mock backtest result dict
- [ ] Strategy registry: `from src.strategies.registry import list_strategies; print(list_strategies())`
- [ ] Config LLM switch: change `llm.provider` in `config.yaml` between `claude` and `minimax`

🤖 Generated with [Claude Code](https://claude.com/claude-code)"""

payload = json.dumps({"title": new_title, "body": new_body}).encode("utf-8")

req = urllib.request.Request(
    "https://api.github.com/repos/happyaicoding/AISMART/pulls/1",
    data=payload,
    headers=headers,
    method="PATCH",
)
with urllib.request.urlopen(req, timeout=15) as r:
    pr = json.loads(r.read())
    print("Updated PR:", pr["html_url"])
    print("New title:", pr["title"])
