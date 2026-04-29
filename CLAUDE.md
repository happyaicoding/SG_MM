# AISMART — AI Strategy Generation System for TAIFEX

> **Read this file first, every session.** It is the single source of truth for project rules.
> For details, see `docs/` (Chinese, on-demand).

## Glossary (術語對照)

Use **code identifiers** (left) in code/config/DB. Use **Chinese terms** in user-facing text and `docs/`.

| Code Identifier | Chinese | Description |
|---|---|---|
| `daytrade_day` | 日盤當沖 | Day-session daytrade (08:45-13:45) |
| `daytrade_night` | 夜盤當沖 | Night-session daytrade (15:00-05:00) |
| `swing_day` | 日盤波段 | Day-session swing (multi-day allowed) |
| `swing_full` | 全日盤波段 | Full-session swing |
| `trading_day` | 交易日 | TAIFEX trading day (night session belongs to next day) |
| `force_close` | 強制平倉 | Mandatory liquidation at 13:40 / 04:45 |
| `cooldown` | 冷卻期 | 20 min before close, no new entries (13:25 / 04:25) |
| `slippage` | 滑價 | Fixed 4 points per trade |
| `WFA` | 走樣外驗證 | Walk-Forward Analysis |
| `OOS` / `IS` | 樣本外/內 | Out-of-Sample / In-Sample windows in WFA |
| `EL` | EasyLanguage | MultiCharts strategy language |
| `MC` | MultiCharts | Trading platform (Windows-only, COM automation) |

## Project Purpose

AI-assisted **research & development** platform for TAIFEX (TX) futures strategies.
Output: validated PLA files for MultiCharts. **Does NOT trade live**.

## Tech Stack

- **Language**: Python 3.11+, JS/TS (frontend)
- **Package mgr**: `uv` (host + container, both Windows and Linux)
- **Backtest**: vectorbt (fast filter) + backtrader (WFA)
- **DB**: SQLite (main) + DuckDB (vector store, embedded)
- **Embeddings**: BAAI/bge-m3 (local, no API cost)
- **LLM**: Nvidia NIM → Minimax M2 → Claude Sonnet 4.6 (cost-first cascade)
- **MC Bridge**: Native Python (pywin32 + pywinauto), MUST run on Windows host
- **Web**: FastAPI + React + shadcn/ui + Vite
- **Deploy**: Docker Compose on Windows host (MC Bridge runs natively, others in containers)

## Directory Structure

```
aismart/
├── CLAUDE.md                     # This file
├── docs/                         # Chinese, on-demand
│   ├── spec.md                   # V1.4 master specification
│   ├── business_rules.md         # TAIFEX business rules
│   ├── architecture.md           # System architecture + legacy reuse
│   ├── el_validation.md          # 3-layer EL validation
│   ├── llm_prompts.md            # 5-stage prompt chain + Few-shot
│   ├── rag_design.md             # Vector store design + retrieval
│   ├── api_contracts.md          # MC Bridge + Web API
│   ├── quality_safeguards.md     # 10 quality mechanisms (READ FIRST!)
│   ├── phases_plan.md            # Phase 0-5 + migration plan
│   ├── issues_to_review.md       # Tracked decisions
│   ├── phase_reports/            # Auto-generated per Phase
│   └── legacy/                   # Old project reference (read-only)
├── src/                          # Source (Phase 0+ creates)
├── tests/                        # pytest
├── data/                         # SQLite, DuckDB, CSV, models
├── library/                      # Few-shot EL examples
├── config/                       # YAML configs
├── docker-compose.yml
└── pyproject.toml
```

## Documentation Index — When to read which

| Task | Read |
|---|---|
| **Starting any Phase** | `docs/quality_safeguards.md` §1 (mandatory checklist) |
| Editing business logic (sessions, slippage, force-close) | `docs/business_rules.md` |
| LLM, RAG, prompt work | `docs/llm_prompts.md` + `docs/rag_design.md` |
| MC Bridge, EL validation | `docs/el_validation.md` + `docs/api_contracts.md` |
| Architecture decisions | `docs/architecture.md` |
| Web UI development | `docs/api_contracts.md` |
| Full spec lookup | `docs/spec.md` |
| Phase scope & deliverables | `docs/phases_plan.md` |

## Core Business Rules (Quick Reference)

| Strategy | Open hours | Cooldown | Force close | Cross-session | Cross-day |
|---|---|---|---|---|---|
| `daytrade_day` | 08:45-13:25 | 13:25-13:40 | **13:40 K open** | ❌ | ❌ |
| `daytrade_night` | 15:00-04:25 | 04:25-04:45 | **04:45 K open** | ❌ | ❌ |
| `swing_day` | 08:45-13:45 only | none | none | ❌ (sees no night data) | ✅ |
| `swing_full` | both sessions | none | none | ✅ | ✅ |

- **Slippage**: 4 points (NTD 800) flat for all trades
- **Trading day**: Night session 15:00-05:00 belongs to **next** trading day
- **Data**: 12 years (2014-01 to 2025-12) of 1-min OHLC, no volume/chip in Phase 1

## Development Rules (Strict)

- **Spec freeze**: Once V1.4 is locked, no scope additions during a Phase. New ideas → `docs/issues_to_review.md`.
- **Phase Gate**: A Phase only ends when its checklist passes 100%. No skipping.
- **Reject fragmentation**: Every commit must map to a current-Phase milestone. No "while I'm here" changes.
- **Legacy code is read-only**: Reuse via whole-block copy or whole-block rewrite. No piecemeal edits to legacy files.
- **Type hints + docstrings (Google style)** mandatory. `black` + `ruff` enforced.
- **Secrets**: only via `.env`. Never hardcoded.
- **Reply in Traditional Chinese** when user writes in Chinese, even if instructions are English.
- **No emoji in print()** (cp950 console encoding on Windows).
- **Time zone**: `Asia/Taipei` system-wide.

## Common Commands

```bash
# Setup (Phase 0)
uv venv && uv pip install -e .
docker-compose up -d --build
python scripts/setup_legacy_assets.py     # copy reusable code from legacy/

# Daily dev
python main.py db init
python main.py data init --csv-dir data/raw/
python main.py web --port 8000
pytest tests/

# MC Bridge (Windows native, separate venv)
python scripts/verify_mc_connection.py
python -m src.mc_bridge.server         # FastAPI service on :8001
```

## Phase Status (auto-updated)

**Active Phase**: ⏳ Phase 1 — 基礎設施與資料層
**Started**: 2026-04-29
**Progress**: ████████░░ 75%

### Recent Milestones (last 10)
- ✅ [P1] feat: trading_day logic + CSV ETL + 13 unit tests
- ✅ [P1] feat: main.py CLI (db-init, data-init, data-count)
- ✅ [P1] feat: DuckDB vector_store.py (三向量 schema + 4 collections + HNSW)
- ✅ [P1] feat: RAG test set (45 cases) + eval_rag.py
- 🔄 [P1] Phase 1 Report 完成待補寫驗收結果
- 🔄 [P1] Phase Gate 驗收（db init, ETL, DuckDB 4 collections, pytest 全綠）

### Quality Metrics (auto-updated each Phase)
- Strategy diversity index: not measured yet
- LLM success rate (per provider): not measured yet
- RAG Recall@5: 0.0 (Phase 3 首批策略入庫後測量)
- Cost per WFA-passing strategy: not measured yet

### Blockers
- None

### Phase 1 完成度
| 模組 | 狀態 |
|---|---|
| .env + Config Loader | ✅ 完成 |
| SQLite Schema + Migration | ✅ 完成 |
| CSV ETL (trading_day + etl.py) | ✅ 完成 |
| main.py CLI | ✅ 完成 |
| DuckDB VectorStore (三向量 schema) | ✅ 完成 |
| DuckDB 4 Collection init + HNSW | ✅ 完成 |
| RAG Test Set (45 cases) | ✅ 完成 |
| eval_rag.py | ✅ 完成 |
| Unit Tests (27 tests 全綠) | ✅ 完成 |
| Phase 1 Report | 🔄 補寫驗收結果 |

## Known Pitfalls (from legacy project)

- **MC EL files MUST be cp950 encoded** when writing to Studies dir (NOT UTF-8)
- **PLEditor must Ctrl+F7 once with Admin** to register the strategy slot, then subsequent overwrites work without Admin
- **`print()` cannot use emoji** in Windows console
- **MC dialogs** must be handled by background thread (`_close_mc_dialogs`); legacy `mc_bridge.py` has the working implementation
- **DuckDB and SQLite can co-exist** in same process, but each connection must be its own session
- **bge-m3 first download is 2.3GB** — pre-download in Docker build or first-run

## Working Style for Claude Code

1. Before any task: re-read this file + relevant `docs/*.md`
2. **Always** read `docs/quality_safeguards.md` §1 before starting a new Phase
3. When unsure: ASK the user. Don't guess.
4. When a strategy/task fails: log the failure, do not crash the batch
5. Update Phase Status section at the end of each work session

## Commit & Branch Convention

- Branches: `main` (stable), `develop` (integration), `feature/phaseN-<topic>`
- Commit format: `[PhaseN] <type>: <subject>`
  - Types: `feat / fix / refactor / docs / test / chore`
  - Example: `[Phase1] feat: CSV ETL with trading_day mapping`
- Tag each Phase end: `phase-N-complete`
