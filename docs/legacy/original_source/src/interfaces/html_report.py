"""Plotly HTML 互動式回測報表。

功能：
    generate_html(strategy_id, result, output_dir)
        接收策略 ID + 回測結果 dict（含 equity Series），
        產生三圖合一的互動式 HTML 報表：
            1. 資金曲線（equity curve）
            2. 月度損益熱力圖（monthly PnL heatmap）
            3. 水下回撤圖（underwater drawdown）

result dict 必要欄位（來自 python_bt.run_backtest()）：
    equity        — pd.Series（index=DatetimeIndex，值=資金）
    sharpe        — float
    max_drawdown  — float（0.22 = 22%）
    profit_factor — float
    total_trades  — int
    win_rate      — float（0.55 = 55%）
    annual_return — float
    final_equity  — float

選用欄位：
    strategy_name — str（顯示於報表標題）
    avg_win       — float
    avg_loss      — float

Usage:
    from src.interfaces.html_report import generate_html

    path = generate_html(
        strategy_id="abc123",
        result=bt_result,          # python_bt.run_backtest() 的回傳值
        output_dir="reports/output/",
    )
    print("報表已儲存至:", path)
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from src.interfaces._report_utils import calc_monthly_pnl, sanitize_filename

logger = logging.getLogger(__name__)

# 嘗試 import plotly；若環境未安裝，延遲到呼叫時才報錯
try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    _HAS_PLOTLY = True
except ImportError:
    _HAS_PLOTLY = False


def generate_html(
    strategy_id: str,
    result: dict,
    output_dir: str = "reports/output/",
    strategy_name: str = "",
) -> str:
    """產生 Plotly HTML 互動報表，回傳儲存路徑。

    Args:
        strategy_id:   策略 UUID 或名稱（用於檔名）
        result:        回測結果 dict（必須含 equity Series）
        output_dir:    輸出目錄（自動建立）
        strategy_name: 顯示於報表標題的策略名稱

    Returns:
        HTML 檔案的絕對路徑字串

    Raises:
        ImportError:   plotly 未安裝
        ValueError:    result 缺少 equity 欄位或 equity 為空
    """
    if not _HAS_PLOTLY:
        raise ImportError(
            "plotly 未安裝。請執行：pip install plotly"
        )

    equity: pd.Series | None = result.get("equity")
    if equity is None or len(equity) == 0:
        raise ValueError("result['equity'] 為空或不存在，無法產生報表")

    # ── 確保 output_dir 存在 ──────────────────────────────────────
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    # ── 準備資料 ──────────────────────────────────────────────────
    title = strategy_name or strategy_id
    dates = equity.index
    equity_vals = equity.values

    # 水下回撤
    rolling_max = equity.cummax()
    drawdown = (equity - rolling_max) / rolling_max  # 負值序列

    monthly_pnl = calc_monthly_pnl(equity)

    # ── 建立 Plotly 子圖佈局 ──────────────────────────────────────
    fig = make_subplots(
        rows=3, cols=1,
        subplot_titles=("資金曲線 (Equity Curve)", "月度損益熱力圖 (Monthly PnL)", "水下回撤 (Drawdown)"),
        row_heights=[0.45, 0.30, 0.25],
        vertical_spacing=0.08,
    )

    # ── 圖 1：資金曲線 ────────────────────────────────────────────
    fig.add_trace(
        go.Scatter(
            x=dates,
            y=equity_vals,
            mode="lines",
            name="資金",
            line=dict(color="#2196F3", width=1.5),
            hovertemplate="日期：%{x}<br>資金：NT$%{y:,.0f}<extra></extra>",
        ),
        row=1, col=1,
    )

    # ── 圖 2：月度損益熱力圖 ──────────────────────────────────────
    if monthly_pnl is not None and not monthly_pnl.empty:
        z_vals, x_labels, y_labels = _heatmap_matrix(monthly_pnl)
        fig.add_trace(
            go.Heatmap(
                z=z_vals,
                x=x_labels,
                y=y_labels,
                colorscale=[
                    [0.0, "#d32f2f"],   # 深紅（虧損）
                    [0.5, "#fafafa"],   # 白（持平）
                    [1.0, "#388e3c"],   # 深綠（獲利）
                ],
                zmid=0,
                colorbar=dict(title="損益(元)", x=1.02),
                hovertemplate="月份：%{x}<br>年份：%{y}<br>損益：NT$%{z:,.0f}<extra></extra>",
                name="月度損益",
            ),
            row=2, col=1,
        )

    # ── 圖 3：水下回撤 ────────────────────────────────────────────
    fig.add_trace(
        go.Scatter(
            x=dates,
            y=drawdown.values * 100,  # 轉為百分比
            mode="lines",
            fill="tozeroy",
            name="回撤%",
            line=dict(color="#f44336", width=1),
            fillcolor="rgba(244,67,54,0.25)",
            hovertemplate="日期：%{x}<br>回撤：%{y:.1f}%<extra></extra>",
        ),
        row=3, col=1,
    )

    # ── 統計摘要注釋（圖 1 右上角）─────────────────────────────────
    stats_text = _build_stats_text(result)
    fig.add_annotation(
        xref="paper", yref="paper",
        x=0.98, y=0.98,
        text=stats_text,
        showarrow=False,
        align="left",
        font=dict(size=11, family="Courier New, monospace"),
        bgcolor="rgba(255,255,255,0.85)",
        bordercolor="#999",
        borderwidth=1,
        borderpad=6,
    )

    # ── 整體佈局 ──────────────────────────────────────────────────
    fig.update_layout(
        title=dict(
            text=f"<b>AISMART 回測報表</b> — {title}",
            font=dict(size=18),
        ),
        height=900,
        template="plotly_white",
        showlegend=False,
        margin=dict(t=80, r=120, b=40, l=60),
    )
    fig.update_yaxes(title_text="NT$", row=1, col=1)
    fig.update_yaxes(title_text="回撤%", row=3, col=1)

    # ── 儲存 HTML ─────────────────────────────────────────────────
    html_path = out_path / f"report_{sanitize_filename(strategy_id)}.html"
    fig.write_html(str(html_path), include_plotlyjs="cdn", full_html=True)

    logger.info("HTML 報表已儲存：%s", html_path)
    return str(html_path)


# ── 內部工具函式 ──────────────────────────────────────────────────────

def _heatmap_matrix(monthly_pnl: pd.Series) -> tuple[list, list, list]:
    """將月度損益 Series 轉換為熱力圖所需的 z/x/y 格式。

    Returns:
        z_vals  — 2D list（年份 × 月份）
        x_labels — 月份標籤 1~12
        y_labels — 年份標籤（字串）
    """
    df = pd.DataFrame({
        "year":  monthly_pnl.index.year,
        "month": monthly_pnl.index.month,
        "pnl":   monthly_pnl.values,
    })
    pivot = df.pivot(index="year", columns="month", values="pnl")
    pivot = pivot.sort_index()

    months = list(range(1, 13))
    pivot = pivot.reindex(columns=months)  # 補齊缺失月份為 NaN

    x_labels = [str(m) for m in months]
    y_labels = [str(y) for y in pivot.index.tolist()]
    z_vals = pivot.values.tolist()

    return z_vals, x_labels, y_labels


def _build_stats_text(result: dict) -> str:
    """組裝績效摘要文字（HTML 格式，用於 Plotly 注釋）。"""
    lines = [
        f"Sharpe Ratio  : {result.get('sharpe', 0.0):.3f}",
        f"Max Drawdown  : {result.get('max_drawdown', 0.0):.1%}",
        f"Profit Factor : {result.get('profit_factor', 0.0):.2f}",
        f"Annual Return : {result.get('annual_return', 0.0):.1%}",
        f"Total Trades  : {result.get('total_trades', 0)}",
        f"Win Rate      : {result.get('win_rate', 0.0):.1%}",
        f"Final Equity  : NT${result.get('final_equity', 0.0):,.0f}",
    ]
    if result.get("avg_win") is not None:
        lines.append(f"Avg Win       : NT${result['avg_win']:,.0f}")
    if result.get("avg_loss") is not None:
        lines.append(f"Avg Loss      : NT${result['avg_loss']:,.0f}")
    return "<br>".join(lines)
