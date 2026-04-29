"""靜態績效報表 — WeasyPrint PDF（未安裝時輸出 HTML）。

功能：
    generate_pdf(strategy_id, result, output_dir)
        接收策略 ID + 回測結果 dict，
        產生靜態文字型績效摘要報表：
            - 安裝 WeasyPrint 時：輸出 PDF（pip install weasyprint）
            - 未安裝時：輸出純 HTML 靜態頁面（含印刷樣式）

result dict 欄位（同 html_report.py）：
    equity, sharpe, max_drawdown, profit_factor, total_trades,
    win_rate, annual_return, final_equity, avg_win, avg_loss（選用）

Usage:
    from src.interfaces.pdf_report import generate_pdf

    path = generate_pdf(
        strategy_id="abc123",
        result=bt_result,
        output_dir="reports/output/",
        strategy_name="MA_Cross v1",
    )
    print("報表已儲存至:", path)

安裝 WeasyPrint（選用）：
    pip install weasyprint
    # Windows 需先安裝 GTK3：https://github.com/tschoonj/GTK-for-Windows-Runtime-Environment-Installer
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src.interfaces._report_utils import calc_monthly_pnl, sanitize_filename

logger = logging.getLogger(__name__)

try:
    import weasyprint as _weasyprint
    _HAS_WEASYPRINT = True
except ImportError:
    _HAS_WEASYPRINT = False


def generate_pdf(
    strategy_id: str,
    result: dict,
    output_dir: str = "reports/output/",
    strategy_name: str = "",
) -> str:
    """產生靜態績效摘要報表，回傳儲存路徑。

    Args:
        strategy_id:   策略 UUID 或名稱（用於檔名）
        result:        回測結果 dict
        output_dir:    輸出目錄（自動建立）
        strategy_name: 顯示於報表標題的策略名稱

    Returns:
        PDF 或 HTML 檔案路徑（字串）

    Notes:
        - 有安裝 weasyprint → 輸出 .pdf
        - 未安裝 weasyprint  → 輸出 .html（含 @media print 樣式，可直接列印）
    """
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    safe_id = sanitize_filename(strategy_id)
    title = strategy_name or strategy_id
    html_content = _build_html(title, result)

    if _HAS_WEASYPRINT:
        pdf_path = out_path / f"report_{safe_id}.pdf"
        _weasyprint.HTML(string=html_content).write_pdf(str(pdf_path))
        logger.info("PDF 報表已儲存：%s", pdf_path)
        return str(pdf_path)
    else:
        html_path = out_path / f"report_{safe_id}_static.html"
        html_path.write_text(html_content, encoding="utf-8")
        logger.info(
            "weasyprint 未安裝，已儲存靜態 HTML：%s（可用瀏覽器列印為 PDF）",
            html_path,
        )
        return str(html_path)


# ── 內部：HTML 產生器 ──────────────────────────────────────────────────

def _build_html(title: str, result: dict) -> str:
    """產生完整 HTML 字串（含內嵌 CSS，支援列印）。"""
    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # 績效指標
    sharpe       = result.get("sharpe", 0.0) or 0.0
    max_dd       = result.get("max_drawdown", 0.0) or 0.0
    pf           = result.get("profit_factor", 0.0) or 0.0
    trades       = result.get("total_trades", 0) or 0
    win_rate     = result.get("win_rate", 0.0) or 0.0
    ann_ret      = result.get("annual_return", 0.0) or 0.0
    final_eq     = result.get("final_equity", 0.0) or 0.0
    avg_win      = result.get("avg_win")
    avg_loss     = result.get("avg_loss")

    # 月度損益表格
    equity: pd.Series | None = result.get("equity")
    monthly_table_html = _build_monthly_table(equity) if equity is not None else ""

    # 顏色判斷
    sharpe_color = "#388e3c" if sharpe >= 1.2 else "#d32f2f"
    dd_color     = "#d32f2f" if max_dd >= 0.35 else "#388e3c"
    pf_color     = "#388e3c" if pf >= 1.0 else "#d32f2f"

    avg_win_row  = f"<tr><td>平均獲利</td><td>NT${avg_win:,.0f}</td></tr>" if avg_win is not None else ""
    avg_loss_row = f"<tr><td>平均虧損</td><td>NT${avg_loss:,.0f}</td></tr>" if avg_loss is not None else ""

    return f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="utf-8">
<title>AISMART 回測報表 — {title}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: "Noto Sans TC", "Microsoft JhengHei", sans-serif;
    font-size: 13px;
    color: #212121;
    background: #fff;
    padding: 32px;
  }}
  h1 {{ font-size: 22px; margin-bottom: 4px; color: #1565c0; }}
  .meta {{ color: #757575; margin-bottom: 24px; font-size: 12px; }}
  h2 {{ font-size: 15px; margin: 20px 0 8px; border-bottom: 1px solid #e0e0e0; padding-bottom: 4px; }}
  table {{ width: 100%; border-collapse: collapse; margin-bottom: 16px; }}
  th, td {{
    border: 1px solid #e0e0e0;
    padding: 6px 10px;
    text-align: right;
  }}
  th {{ background: #f5f5f5; text-align: center; font-weight: 600; }}
  td:first-child {{ text-align: left; }}
  .kv-table td {{ width: 50%; }}
  .highlight {{ font-weight: bold; }}
  .pos {{ color: #388e3c; }}
  .neg {{ color: #d32f2f; }}
  .footer {{
    margin-top: 32px; font-size: 11px; color: #9e9e9e;
    border-top: 1px solid #e0e0e0; padding-top: 8px;
  }}
  @media print {{
    body {{ padding: 16px; }}
    @page {{ margin: 1cm; }}
  }}
</style>
</head>
<body>
  <h1>AISMART 回測績效報表</h1>
  <p class="meta">策略：{title} &nbsp;|&nbsp; 產生時間：{now}</p>

  <h2>核心績效指標</h2>
  <table class="kv-table">
    <tr>
      <td>Sharpe Ratio</td>
      <td class="highlight" style="color:{sharpe_color}">{sharpe:.4f}</td>
    </tr>
    <tr>
      <td>最大回撤 (Max Drawdown)</td>
      <td class="highlight" style="color:{dd_color}">{max_dd:.1%}</td>
    </tr>
    <tr>
      <td>獲利因子 (Profit Factor)</td>
      <td class="highlight" style="color:{pf_color}">{pf:.4f}</td>
    </tr>
    <tr><td>年化報酬率</td><td>{ann_ret:.2%}</td></tr>
    <tr><td>總交易次數</td><td>{trades}</td></tr>
    <tr><td>勝率 (Win Rate)</td><td>{win_rate:.1%}</td></tr>
    <tr><td>最終資金</td><td>NT${final_eq:,.0f}</td></tr>
    {avg_win_row}
    {avg_loss_row}
  </table>

  <h2>篩選門檻對照</h2>
  <table>
    <tr>
      <th>指標</th><th>當前值</th><th>Python 初篩門檻</th><th>結果</th>
    </tr>
    <tr>
      <td>Sharpe Ratio</td>
      <td>{sharpe:.3f}</td>
      <td>&gt;= 1.2</td>
      <td class="{'pos' if sharpe >= 1.2 else 'neg'}">{'通過' if sharpe >= 1.2 else '未通過'}</td>
    </tr>
    <tr>
      <td>Max Drawdown</td>
      <td>{max_dd:.1%}</td>
      <td>&lt;= 35%</td>
      <td class="{'pos' if max_dd <= 0.35 else 'neg'}">{'通過' if max_dd <= 0.35 else '未通過'}</td>
    </tr>
    <tr>
      <td>Profit Factor</td>
      <td>{pf:.2f}</td>
      <td>&gt;= 1.0</td>
      <td class="{'pos' if pf >= 1.0 else 'neg'}">{'通過' if pf >= 1.0 else '未通過'}</td>
    </tr>
    <tr>
      <td>Total Trades</td>
      <td>{trades}</td>
      <td>&gt;= 80</td>
      <td class="{'pos' if trades >= 80 else 'neg'}">{'通過' if trades >= 80 else '未通過'}</td>
    </tr>
  </table>

  {monthly_table_html}

  <p class="footer">
    Generated by AISMART &nbsp;|&nbsp;
    Powered by Claude AI &nbsp;|&nbsp;
    https://github.com/your-repo/AISMART
  </p>
</body>
</html>"""


def _build_monthly_table(equity: pd.Series) -> str:
    """產生月度損益 HTML 表格。"""
    try:
        monthly = calc_monthly_pnl(equity)
        if monthly is None or len(monthly) == 0:
            return ""

        df = pd.DataFrame({
            "year":  monthly.index.year,
            "month": monthly.index.month,
            "pnl":   monthly.values,
        })
        pivot = df.pivot(index="year", columns="month", values="pnl")
        pivot = pivot.sort_index().reindex(columns=range(1, 13))

        # 月份標題
        month_names = ["1月","2月","3月","4月","5月","6月",
                       "7月","8月","9月","10月","11月","12月"]
        header_cells = "".join(f"<th>{m}</th>" for m in month_names)
        header = f"<tr><th>年度</th>{header_cells}<th>年度合計</th></tr>"

        rows = []
        for year, row in pivot.iterrows():
            year_total = row.sum(skipna=True)
            cells = []
            for m in range(1, 13):
                val = row.get(m, float("nan"))
                if pd.isna(val):
                    cells.append("<td>—</td>")
                else:
                    cls = "pos" if val >= 0 else "neg"
                    cells.append(f'<td class="{cls}">NT${val:,.0f}</td>')
            total_cls = "pos" if year_total >= 0 else "neg"
            rows.append(
                f"<tr><td>{year}</td>{''.join(cells)}"
                f'<td class="{total_cls}"><b>NT${year_total:,.0f}</b></td></tr>'
            )

        return (
            "<h2>月度損益明細 (Monthly PnL)</h2>"
            f"<table><thead>{header}</thead><tbody>{''.join(rows)}</tbody></table>"
        )
    except Exception as exc:
        logger.warning("月度損益表格產生失敗：%s", exc)
        return ""
