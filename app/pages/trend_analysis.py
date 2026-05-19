"""Trend Analysis page — longitudinal QC patterns and auto-generated insights."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from app.db import is_demo_mode


# ── data loaders ──────────────────────────────────────────────────────────────


def _load_summary(start_date: Optional[date], end_date: Optional[date]) -> pd.DataFrame:
    if is_demo_mode():
        from app.demo_data import get_run_qc_summary_demo
        return get_run_qc_summary_demo(start_date=start_date, end_date=end_date, limit=500)
    # Live DB: fall back to the standard query (no avg_ct_value in live mode yet)
    from app.queries import get_run_qc_summary
    return get_run_qc_summary(start_date=start_date, end_date=end_date, limit=500)


def _load_metric_breakdown(start_date: Optional[date], end_date: Optional[date]) -> pd.DataFrame:
    if is_demo_mode():
        from app.demo_data import get_metric_breakdown_demo
        return get_metric_breakdown_demo(start_date=start_date, end_date=end_date)
    return pd.DataFrame()   # placeholder for live DB


# ── auto-insight engine ───────────────────────────────────────────────────────

_FLAG_COLORS = {"PASS": "#2ecc71", "WARN": "#f39c12", "FAIL": "#e74c3c"}
_STACKED_COLORS = {"pass": "#2ecc71", "warn": "#f39c12", "fail": "#e74c3c"}


def _auto_insights(summary_df: pd.DataFrame, breakdown_df: pd.DataFrame) -> list[tuple[str, str]]:
    """Return a list of (icon_label, message) insight tuples."""
    insights: list[tuple[str, str]] = []
    if summary_df.empty:
        return insights

    df = summary_df.sort_values("started_at").copy()
    n = len(df)

    # 1. Overall fail rate trend (first half vs second half)
    first_half = df.head(n // 2)["fail_rate"].mean()
    second_half = df.tail(n - n // 2)["fail_rate"].mean()
    if pd.notna(first_half) and pd.notna(second_half):
        if second_half > first_half * 1.2:
            insights.append((
                "⚠️ 失败率上升",
                f"近期 runs 平均失败率 **{second_half*100:.1f}%**，"
                f"高于前期 {first_half*100:.1f}%——质量有下滑趋势，建议排查仪器和试剂状态。",
            ))
        elif second_half < first_half * 0.8:
            insights.append((
                "✅ 质量改善",
                f"近期 runs 失败率 **{second_half*100:.1f}%**，"
                f"低于前期 {first_half*100:.1f}%——质量控制正在改善。",
            ))

    # 2. Worst / best instrument
    if "instrument_name" in df.columns:
        inst_fail = df.groupby("instrument_name")["fail_rate"].mean().dropna()
        if len(inst_fail) >= 2:
            worst = inst_fail.idxmax()
            best = inst_fail.idxmin()
            worst_rate = inst_fail[worst]
            best_rate = inst_fail[best]
            if worst_rate > 0.08:
                insights.append((
                    "🔧 仪器异常",
                    f"**{worst}** 平均失败率 {worst_rate*100:.1f}%，"
                    f"显著高于 {best}（{best_rate*100:.1f}%）——建议优先检查该仪器的维护记录。",
                ))

    # 3. Ct value drift
    if "avg_ct_value" in df.columns:
        ct_data = df.dropna(subset=["avg_ct_value"])
        if len(ct_data) >= 6:
            first_ct = ct_data.head(5)["avg_ct_value"].mean()
            last_ct = ct_data.tail(5)["avg_ct_value"].mean()
            if last_ct - first_ct > 0.5:
                insights.append((
                    "📈 Ct 值漂移",
                    f"近5次 runs 平均 Ct = **{last_ct:.2f}**，"
                    f"较最早5次（{first_ct:.2f}）上升了 +{last_ct-first_ct:.2f}——"
                    f"可能存在试剂批次老化或新试剂批次切换，请核查。",
                ))
            elif first_ct - last_ct > 0.5:
                insights.append((
                    "✅ Ct 值重置",
                    f"Ct 均值已从 {first_ct:.2f} 降回 **{last_ct:.2f}**——"
                    f"试剂批次更换后质量已恢复正常。",
                ))

    # 4. NTC contamination
    if not breakdown_df.empty:
        ntc_row = breakdown_df[breakdown_df["metric_name"] == "NTC_check"]
        if not ntc_row.empty:
            ntc_fail = int(ntc_row["fail_count"].values[0])
            ntc_total = int(ntc_row["total_count"].values[0])
            if ntc_fail > 0:
                ntc_rate = ntc_fail / ntc_total * 100
                insights.append((
                    "🧪 NTC 污染警告",
                    f"检测到 **{ntc_fail}** 次 NTC_check 失败（占 {ntc_rate:.1f}%）——"
                    f"存在阴性对照孔污染风险，请检查样本处理流程和实验室环境。",
                ))

    # 5. Most problematic assay
    if "assay_name" in df.columns:
        assay_fail = df.groupby("assay_name")["fail_rate"].mean().dropna()
        if len(assay_fail) >= 2:
            worst_assay = assay_fail.idxmax()
            worst_assay_rate = assay_fail[worst_assay]
            if worst_assay_rate > 0.10:
                insights.append((
                    "🔬 高失败率 Assay",
                    f"**{worst_assay}** 平均失败率 {worst_assay_rate*100:.1f}%，"
                    f"在所有 assay 中最高——建议重新评估该 assay 的操作方案。",
                ))

    return insights


# ── chart helpers ─────────────────────────────────────────────────────────────


def _fail_rate_trend_chart(df: pd.DataFrame, color_by: str) -> go.Figure:
    fig = px.line(
        df,
        x="started_at",
        y="fail_rate_pct",
        color=color_by,
        markers=True,
        labels={"started_at": "Run 日期", "fail_rate_pct": "失败率 (%)"},
        color_discrete_sequence=px.colors.qualitative.Set2,
    )
    fig.update_traces(marker_size=6, line_width=2)
    fig.update_layout(
        height=360,
        margin=dict(l=0, r=0, t=10, b=0),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(showgrid=True, gridcolor="#e0e0e0"),
        yaxis=dict(showgrid=True, gridcolor="#e0e0e0", rangemode="tozero"),
    )
    return fig


def _ct_drift_chart(df: pd.DataFrame) -> go.Figure:
    ct_df = df.dropna(subset=["avg_ct_value"]).copy()
    fig = px.line(
        ct_df,
        x="started_at",
        y="avg_ct_value",
        color="assay_name",
        markers=True,
        labels={"started_at": "Run 日期", "avg_ct_value": "平均 Ct 值"},
        color_discrete_sequence=px.colors.qualitative.Pastel,
    )
    # Annotate the reset point (run 26)
    reset_rows = ct_df[ct_df["run_name"] >= "RUN-2026-026"]
    if not reset_rows.empty:
        reset_x = reset_rows.iloc[0]["started_at"]
        fig.add_vline(
            x=reset_x,
            line_dash="dot",
            line_color="#888",
            annotation_text="新试剂批次",
            annotation_position="top right",
            annotation_font_size=11,
        )
    fig.update_traces(marker_size=5, line_width=1.8)
    fig.update_layout(
        height=320,
        margin=dict(l=0, r=0, t=20, b=0),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(showgrid=True, gridcolor="#e0e0e0"),
        yaxis=dict(showgrid=True, gridcolor="#e0e0e0"),
    )
    return fig


def _instrument_stacked_chart(df: pd.DataFrame) -> go.Figure:
    inst_df = (
        df.groupby("instrument_name")[["pass_count", "warn_count", "fail_count"]]
        .sum()
        .reset_index()
    )
    fig = go.Figure()
    for col, label, color in [
        ("pass_count", "PASS", "#2ecc71"),
        ("warn_count", "WARN", "#f39c12"),
        ("fail_count", "FAIL", "#e74c3c"),
    ]:
        fig.add_trace(go.Bar(
            name=label,
            x=inst_df["instrument_name"],
            y=inst_df[col],
            marker_color=color,
            text=inst_df[col],
            textposition="inside",
        ))
    fig.update_layout(
        barmode="stack",
        height=320,
        margin=dict(l=0, r=0, t=10, b=0),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        xaxis_title="仪器",
        yaxis_title="QC 检查次数",
        yaxis=dict(showgrid=True, gridcolor="#e0e0e0"),
    )
    return fig


def _metric_fail_rate_chart(breakdown_df: pd.DataFrame) -> go.Figure:
    df = breakdown_df.copy()
    df["fail_rate_pct"] = (df["fail_count"] / df["total_count"] * 100).round(1)
    df["warn_rate_pct"] = (df["warn_count"] / df["total_count"] * 100).round(1)
    df = df.sort_values("fail_rate_pct", ascending=True)

    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="FAIL",
        y=df["metric_name"],
        x=df["fail_rate_pct"],
        orientation="h",
        marker_color="#e74c3c",
        text=df["fail_rate_pct"].apply(lambda v: f"{v:.1f}%"),
        textposition="outside",
    ))
    fig.add_trace(go.Bar(
        name="WARN",
        y=df["metric_name"],
        x=df["warn_rate_pct"],
        orientation="h",
        marker_color="#f39c12",
        text=df["warn_rate_pct"].apply(lambda v: f"{v:.1f}%"),
        textposition="outside",
    ))
    fig.update_layout(
        barmode="stack",
        height=260,
        margin=dict(l=0, r=60, t=10, b=0),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        xaxis_title="占比 (%)",
        xaxis=dict(showgrid=True, gridcolor="#e0e0e0"),
    )
    return fig


def _operator_table(df: pd.DataFrame) -> pd.DataFrame:
    op_df = (
        df.groupby("operator")
        .agg(
            runs=("run_id", "count"),
            avg_fail_rate=("fail_rate", "mean"),
            total_samples=("sample_count", "sum"),
        )
        .reset_index()
    )
    op_df["avg_fail_rate"] = (op_df["avg_fail_rate"] * 100).round(1)
    op_df.columns = ["操作员", "Run 数", "平均失败率 (%)", "总样本数"]
    return op_df.sort_values("平均失败率 (%)")


# ── page renderer ─────────────────────────────────────────────────────────────


def render() -> None:
    st.header("趋势分析")

    # ── sidebar filters ───────────────────────────────────────────────────────
    today = date.today()
    default_start = today - timedelta(days=90)
    date_range = st.sidebar.date_input(
        "日期范围",
        (default_start, today),
        help="按 run 开始时间筛选",
    )
    if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
        start_date, end_date = date_range
    else:
        start_date, end_date = default_start, today

    # ── load data ─────────────────────────────────────────────────────────────
    summary_df = _load_summary(start_date, end_date)
    breakdown_df = _load_metric_breakdown(start_date, end_date)

    if summary_df.empty:
        st.info("所选日期范围内无数据。")
        return

    summary_df["started_at"] = pd.to_datetime(summary_df["started_at"])
    summary_df = summary_df.sort_values("started_at").copy()
    summary_df["fail_rate"] = pd.to_numeric(summary_df["fail_rate"], errors="coerce")
    summary_df["fail_rate_pct"] = (summary_df["fail_rate"] * 100).round(2)

    # ── auto-insights ─────────────────────────────────────────────────────────
    insights = _auto_insights(summary_df, breakdown_df)
    if insights:
        st.subheader("🔍 自动洞察")
        cols = st.columns(min(len(insights), 2))
        for idx, (icon_label, msg) in enumerate(insights):
            with cols[idx % 2]:
                st.info(f"**{icon_label}**\n\n{msg}")
        st.divider()

    # ── KPI cards ─────────────────────────────────────────────────────────────
    total_runs = len(summary_df)
    avg_fail = summary_df["fail_rate_pct"].mean()
    failed_count = int((summary_df["status"] == "FAILED").sum())
    worst_inst = (
        summary_df.groupby("instrument_name")["fail_rate_pct"].mean().idxmax()
        if "instrument_name" in summary_df.columns
        else "—"
    )

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("📋 总 Run 数", total_runs)
    k2.metric("📊 平均失败率", f"{avg_fail:.1f}%")
    k3.metric("❌ 失败 Runs", failed_count)
    k4.metric("🔧 最差仪器", worst_inst)

    st.divider()

    # ── fail rate trend ───────────────────────────────────────────────────────
    st.subheader("失败率趋势")
    col_chart, col_opt = st.columns([4, 1])
    color_by = col_opt.selectbox(
        "颜色区分",
        options=["instrument_name", "assay_name", "operator"],
        format_func=lambda x: {"instrument_name": "仪器", "assay_name": "Assay", "operator": "操作员"}[x],
        key="trend_color",
    )
    col_chart.plotly_chart(_fail_rate_trend_chart(summary_df, color_by), use_container_width=True)

    # ── Ct drift chart ────────────────────────────────────────────────────────
    if "avg_ct_value" in summary_df.columns and summary_df["avg_ct_value"].notna().any():
        st.subheader("Ct 值趋势（试剂批次追踪）")
        st.caption("灰色虚线标注新试剂批次引入点；上升趋势提示试剂老化或标准品漂移。")
        st.plotly_chart(_ct_drift_chart(summary_df), use_container_width=True)

    # ── instrument comparison ─────────────────────────────────────────────────
    if "instrument_name" in summary_df.columns:
        st.subheader("仪器 QC 表现对比")
        c1, c2 = st.columns(2)
        c1.plotly_chart(_instrument_stacked_chart(summary_df), use_container_width=True)

        # Fail rate by instrument as a simple table
        inst_table = (
            summary_df.groupby("instrument_name")
            .agg(
                runs=("run_id", "count"),
                avg_fail_rate=("fail_rate_pct", "mean"),
                total_samples=("sample_count", "sum"),
            )
            .round(1)
            .reset_index()
        )
        inst_table.columns = ["仪器", "Runs", "平均失败率 (%)", "总样本"]
        inst_table = inst_table.sort_values("平均失败率 (%)", ascending=False)
        c2.markdown("**各仪器统计**")
        c2.dataframe(inst_table, use_container_width=True, hide_index=True)

    # ── metric breakdown ──────────────────────────────────────────────────────
    if not breakdown_df.empty:
        st.subheader("QC 指标失败分布")
        st.caption("哪项指标最常触发警告 / 失败？")
        st.plotly_chart(_metric_fail_rate_chart(breakdown_df), use_container_width=True)

    # ── operator performance ──────────────────────────────────────────────────
    if "operator" in summary_df.columns:
        st.subheader("操作员表现")
        st.dataframe(_operator_table(summary_df), use_container_width=True, hide_index=True)
