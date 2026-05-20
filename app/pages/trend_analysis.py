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


def _auto_insights(summary_df: pd.DataFrame, breakdown_df: pd.DataFrame) -> list[tuple[str, str]]:
    """Return a list of (label, message) insight tuples."""
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
                "Fail Rate Rising",
                f"Recent runs average fail rate **{second_half*100:.1f}%**, "
                f"up from {first_half*100:.1f}% in earlier runs. "
                f"Consider reviewing instrument maintenance and reagent status.",
            ))
        elif second_half < first_half * 0.8:
            insights.append((
                "Quality Improving",
                f"Recent runs fail rate **{second_half*100:.1f}%**, "
                f"down from {first_half*100:.1f}% — QC performance is trending better.",
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
                    "Instrument Alert",
                    f"**{worst}** average fail rate {worst_rate*100:.1f}%, "
                    f"significantly higher than {best} ({best_rate*100:.1f}%). "
                    f"Review maintenance records for {worst}.",
                ))

    # 3. Ct value drift
    if "avg_ct_value" in df.columns:
        ct_data = df.dropna(subset=["avg_ct_value"])
        if len(ct_data) >= 6:
            first_ct = ct_data.head(5)["avg_ct_value"].mean()
            last_ct = ct_data.tail(5)["avg_ct_value"].mean()
            if last_ct - first_ct > 0.5:
                insights.append((
                    "Ct Drift Detected",
                    f"Recent 5 runs mean Ct = **{last_ct:.2f}**, "
                    f"up from {first_ct:.2f} in earlier runs (+{last_ct-first_ct:.2f}). "
                    f"Possible reagent lot aging or standard drift — check lot records.",
                ))
            elif first_ct - last_ct > 0.5:
                insights.append((
                    "Ct Value Reset",
                    f"Ct mean has dropped from {first_ct:.2f} back to **{last_ct:.2f}** — "
                    f"quality appears restored after reagent lot change.",
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
                    "NTC Contamination",
                    f"**{ntc_fail}** NTC_check failure(s) detected ({ntc_rate:.1f}% of checks). "
                    f"Negative control wells may be contaminated — review sample handling.",
                ))

    # 5. Most problematic assay
    if "assay_name" in df.columns:
        assay_fail = df.groupby("assay_name")["fail_rate"].mean().dropna()
        if len(assay_fail) >= 2:
            worst_assay = assay_fail.idxmax()
            worst_assay_rate = assay_fail[worst_assay]
            if worst_assay_rate > 0.10:
                insights.append((
                    "High-Fail Assay",
                    f"**{worst_assay}** average fail rate {worst_assay_rate*100:.1f}%, "
                    f"highest among all assays. Consider revising the protocol.",
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
        labels={"started_at": "Run Date", "fail_rate_pct": "Fail Rate (%)"},
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
    ct_df = ct_df.sort_values("started_at")

    fig = px.line(
        ct_df,
        x="started_at",
        y="avg_ct_value",
        color="assay_name",
        markers=True,
        labels={"started_at": "Run Date", "avg_ct_value": "Mean Ct Value"},
        color_discrete_sequence=px.colors.qualitative.Pastel,
    )

    # Annotate the reagent lot reset point (run 26: RUN-2026-026)
    reset_rows = ct_df[ct_df["run_name"] >= "RUN-2026-026"]
    if not reset_rows.empty:
        reset_x = reset_rows.iloc[0]["started_at"]
        # Convert to ISO string — plotly requires string for datetime vline
        reset_x_str = pd.Timestamp(reset_x).isoformat()
        fig.add_vline(
            x=reset_x_str,
            line_dash="dot",
            line_color="#888",
            annotation_text="New reagent lot",
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
        xaxis_title="Instrument",
        yaxis_title="QC Check Count",
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
        xaxis_title="Rate (%)",
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
    op_df.columns = ["Operator", "Runs", "Avg Fail Rate (%)", "Total Samples"]
    return op_df.sort_values("Avg Fail Rate (%)")


# ── page renderer ─────────────────────────────────────────────────────────────


def render() -> None:
    st.header("Trend Analysis")

    # ── sidebar filters ───────────────────────────────────────────────────────
    today = date.today()
    default_start = today - timedelta(days=90)
    date_range = st.sidebar.date_input(
        "Date range",
        (default_start, today),
        help="Filter by run start time",
    )
    if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
        start_date, end_date = date_range
    else:
        start_date, end_date = default_start, today

    # ── load data ─────────────────────────────────────────────────────────────
    summary_df = _load_summary(start_date, end_date)
    breakdown_df = _load_metric_breakdown(start_date, end_date)

    if summary_df.empty:
        st.info("No data available for the selected date range.")
        return

    summary_df["started_at"] = pd.to_datetime(summary_df["started_at"])
    summary_df = summary_df.sort_values("started_at").copy()
    summary_df["fail_rate"] = pd.to_numeric(summary_df["fail_rate"], errors="coerce")
    summary_df["fail_rate_pct"] = (summary_df["fail_rate"] * 100).round(2)

    # ── auto-insights ─────────────────────────────────────────────────────────
    insights = _auto_insights(summary_df, breakdown_df)
    if insights:
        st.subheader("Key Findings")
        cols = st.columns(min(len(insights), 2))
        for idx, (label, msg) in enumerate(insights):
            with cols[idx % 2]:
                st.info(f"**{label}**\n\n{msg}")
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
    k1.metric("Total Runs", total_runs)
    k2.metric("Avg Fail Rate", f"{avg_fail:.1f}%")
    k3.metric("Failed Runs", failed_count)
    k4.metric("Worst Instrument", worst_inst)

    st.divider()

    # ── fail rate trend ───────────────────────────────────────────────────────
    st.subheader("Fail Rate Trend")
    col_chart, col_opt = st.columns([4, 1])
    color_by = col_opt.selectbox(
        "Color by",
        options=["instrument_name", "assay_name", "operator"],
        format_func=lambda x: {
            "instrument_name": "Instrument",
            "assay_name": "Assay",
            "operator": "Operator",
        }[x],
        key="trend_color",
    )
    col_chart.plotly_chart(_fail_rate_trend_chart(summary_df, color_by), use_container_width=True)

    # ── Ct drift chart ────────────────────────────────────────────────────────
    if "avg_ct_value" in summary_df.columns and summary_df["avg_ct_value"].notna().any():
        st.subheader("Ct Value Trend (Reagent Lot Tracking)")
        st.caption(
            "Dashed grey line marks the introduction of a new reagent lot. "
            "An upward trend may indicate reagent aging or standard drift."
        )
        st.plotly_chart(_ct_drift_chart(summary_df), use_container_width=True)

    # ── instrument comparison ─────────────────────────────────────────────────
    if "instrument_name" in summary_df.columns:
        st.subheader("Instrument QC Performance")
        c1, c2 = st.columns(2)
        c1.plotly_chart(_instrument_stacked_chart(summary_df), use_container_width=True)

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
        inst_table.columns = ["Instrument", "Runs", "Avg Fail Rate (%)", "Total Samples"]
        inst_table = inst_table.sort_values("Avg Fail Rate (%)", ascending=False)
        c2.markdown("**Instrument summary**")
        c2.dataframe(inst_table, use_container_width=True, hide_index=True)

    # ── metric breakdown ──────────────────────────────────────────────────────
    if not breakdown_df.empty:
        st.subheader("QC Metric Failure Distribution")
        st.caption("Which metrics trigger the most warnings or failures?")
        st.plotly_chart(_metric_fail_rate_chart(breakdown_df), use_container_width=True)

    # ── operator performance ──────────────────────────────────────────────────
    if "operator" in summary_df.columns:
        st.subheader("Operator Performance")
        st.dataframe(_operator_table(summary_df), use_container_width=True, hide_index=True)
