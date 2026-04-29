# Phase 2 工作說明書（回測引擎與業務規則）

## Phase 2 入口檢查（quality_safeguards.md §1.1）

- [x] 我已讀完 docs/quality_safeguards.md §1
- [x] 本 Phase 涵蓋的品質機制：#2 分層檢索（Phase 1 schema 已建立，Phase 2 驗證業務規則整合）、#5 多樣性（檢索時框架已完成）
- [x] 本 Phase 不該動的品質機制：#6 5段 prompt、#7 儲備遞進、#8 EL 三層驗證、#9 多樣性指標、#10 成本追蹤（Phase 3-5 才實作）
- [x] 本 Phase 結束前必須確認的品質指標：4 種 trading_session 業務規則正確、強平觸發時間精準、vectorbt/backtrader KPI 一致

## 我的承諾

我在本 Phase 內：
- 不會跳過任一業務規則的 unit test
- 不會在兩個引擎中出現不一致的規則實作
- 不會跳過 WFA 合格判定門檻（overfitting_flag）
- 不會忽略滑價 4 點的影響
- 每完成一個模組都會寫 unit test
- 不會在 Phase 2 實作 LLM 生成相關功能

---

## Phase 2 模組清單

| 模組 | 工時 | 檔案 | 狀態 |
|---|---|---|---|
| 業務規則引擎（時段、冷卻、強平） | 4h | src/core/backtest/business_rules.py | 待辦 |
| 滑價計算（統一 4 點） | 1h | src/core/backtest/slippage.py | 待辦 |
| vectorbt 快篩引擎 | 5h | src/core/backtest/vectorbt_filter.py | 待辦 |
| backtrader WFA 引擎 | 7h | src/core/backtest/backtrader_wfa.py | 待辦 |
| KPI 計算模組（6 指標） | 3h | src/core/backtest/kpi.py | 待辦 |
| 篩選門檻判定 + WFA 合格判定 | 2h | src/core/backtest/threshold.py | 待辦 |
| 回測結果寫入 DB | 3h | src/core/backtest/results_writer.py | 待辦 |
| 並行化（multiprocessing.Pool） | 2h | src/core/backtest/parallel.py | 待辦 |
| Unit test（含合成資料 case） | 2h | tests/unit/backtest/ | 待辦 |
| Phase 2 工作說明書 | 1h | docs/phase_reports/phase_2_report.md | 待辦 |
| **合計** | **30h** | | |

---

## Phase Gate 驗收清單

- [ ] vectorbt 跑 1 個 Few-shot 策略，KPI 結果與舊專案一致（誤差 < 5%）
- [ ] backtrader 跑同策略 21 個 WFA 窗口，結果寫入 SQLite
- [ ] 4 種 trading_session 各 1 個測試 case 通過
- [ ] 強平機制在 13:39 / 04:44 觸發點正確
- [ ] 滑價在每筆交易扣 4 點
- [ ] 50 個策略並行批次跑通
- [ ] Phase 2 工作說明書寫完

---

## Phase Gate 驗收結果（Phase 2 結束後填入）

待填入。
