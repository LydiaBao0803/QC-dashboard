from datetime import date, timedelta
from pathlib import Path
from typing import Iterable, List, Optional

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app.db import fetch_df
from app.queries import get_run_qc_summary


# ── helpers ───────────────────────────────────────────────────────────────────


def _assay_selector() -> Optional[int]:
    from app.db import is_demo_mode
    if is_demo_mode():
        from app.demo_data import get_assays_demo
        assays = get_assays_demo()
    else:
        assays = fetch_df("SELECT assay_id, name FROM assay ORDER BY name")
    if assays.empty:
        st.sidebar.warning("No assays found in database.")
        return None

    options: List[str] = ["All assays"]
    label_to_id: dict[str, int] = {}
    for _, row in assays.iterrows():
        label = f"{row['name']} (id={row['assay_id']})"
        options.append(label)
        label_to_id[label] = int(row["assay_id"])

    selected_label = st.sidebar.selectbox("Assay", options=options, index=0)
    return label_to_id.get(selected_label)


def _quick_insights(summary_df: pd.DataFrame) -> list[str]:
    """Return up to 3 short insight strings for the run overview."""
    insights: list[str] = []
    if summary_df.empty or "fail_rate" not in summary_df.columns:
        return insights

    df = summary_df.sort_values("started_at").copy()
    df["fail_rate"] = pd.to_numeric(df["fail_rate"], errors="coerce")

    # 1. Any runs with >20 % fail rate?
    bad_runs = df[df["fail_rate"] > 0.20]
    if not bad_runs.empty:
        names = ", ".join(bad_runs["run_name"].tolist()[:3])
        insights.append(f"{len(bad_runs)} run(s) with fail rate above 20%: {names}")

    # 2. Worst instrument in this filter window
    if "instrument_name" in df.columns:
        inst_fail = df.groupby("instrument_name")["fail_rate"].mean().dropna()
        if len(inst_fail) >= 2:
            worst = inst_fail.idxmax()
            best = inst_fail.idxmin()
            if inst_fail[worst] > 0.08:
                insights.append(
                    f"{worst} average fail rate {inst_fail[worst]*100:.1f}%, "
                    f"higher than {best} ({inst_fail[best]*100:.1f}%)"
                )

    # 3. Recent trend (last 5 vs previous 5)
    if len(df) >= 10:
        prev5 = df.iloc[-10:-5]["fail_rate"].mean()
        last5 = df.iloc[-5:]["fail_rate"].mean()
        if pd.notna(prev5) and pd.notna(last5):
            if last5 > prev5 * 1.25:
                insights.append(
                    f"Recent 5 runs fail rate ({last5*100:.1f}%) higher than prior 5 "
                    f"({prev5*100:.1f}%) — quality may be declining"
                )
            elif last5 < prev5 * 0.75:
                insights.append(
                    f"Recent 5 runs fail rate ({last5*100:.1f}%) lower than prior 5 "
                    f"({prev5*100:.1f}%) — quality is improving"
                )

    return insights[:3]


def _stacked_bar_chart(summary_df: pd.DataFrame) -> go.Figure:
    """Plotly stacked bar: pass / warn / fail per run, ordered by date."""
    df = summary_df.sort_values("started_at").copy()
    labels = df["run_name"].tolist()

    fig = go.Figure()
    for col, label, color in [
        ("pass_count", "PASS", "#2ecc71"),
        ("warn_count", "WARN", "#f39c12"),
        ("fail_count", "FAIL", "#e74c3c"),
    ]:
        if col in df.columns:
            fig.add_trace(go.Bar(
                name=label,
                x=labels,
                y=df[col],
                marker_color=color,
                hovertemplate=f"<b>%{{x}}</b><br>{label}: %{{y}}<extra></extra>",
            ))

    fig.update_layout(
        barmode="stack",
        height=340,
        margin=dict(l=0, r=0, t=10, b=60),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(
            showgrid=False,
            tickangle=-45,
            tickfont=dict(size=10),
        ),
        yaxis=dict(showgrid=True, gridcolor="#e0e0e0", title="QC Check Count"),
    )
    return fig


# ── page renderer ─────────────────────────────────────────────────────────────


def render() -> None:
    st.header("Run Overview")

    today = date.today()
    default_start = today - timedelta(days=90)
    date_val = st.sidebar.date_input(
        "Date range",
        (default_start, today),
        help="Filter by run start time (inclusive)",
    )
    if isinstance(date_val, (list, tuple)) and len(date_val) == 2:
        start_date, end_date = date_val
    else:
        start_date, end_date = default_start, today

    status_choices = ["SCHEDULED", "RUNNING", "COMPLETED", "FAILED"]
    selected_statuses: Iterable[str] = st.sidebar.multiselect(
        "Run status",
        options=status_choices,
        default=["COMPLETED", "FAILED"],
    )

    assay_id = _assay_selector()

    summary_df = get_run_qc_summary(
        start_date=start_date,
        end_date=end_date,
        assay_id=assay_id,
        statuses=selected_statuses,
        limit=200,
    )

    if summary_df.empty:
        st.info("No run data for the selected filters.")
        return

    summary_df["fail_rate"] = pd.to_numeric(summary_df["fail_rate"], errors="coerce")
    summary_df["fail_rate_pct"] = (summary_df["fail_rate"] * 100).round(1)

    # ── KPI cards ─────────────────────────────────────────────────────────────
    total = len(summary_df)
    avg_fail = summary_df["fail_rate_pct"].mean()
    failed_runs = int((summary_df["status"] == "FAILED").sum())
    best_assay = (
        summary_df.groupby("assay_name")["fail_rate"].mean().idxmin()
        if "assay_name" in summary_df.columns and not summary_df["fail_rate"].isna().all()
        else "—"
    )

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Total Runs", total)
    k2.metric("Avg Fail Rate", f"{avg_fail:.1f}%")
    k3.metric("Failed Runs", failed_runs)
    k4.metric("Best Assay", best_assay)

    # ── quick insights ─────────────────────────────────────────────────────────
    insights = _quick_insights(summary_df)
    if insights:
        for msg in insights:
            st.info(msg)

    st.divider()

    # ── stacked bar chart ──────────────────────────────────────────────────────
    st.subheader("QC Result Distribution per Run (Pass / Warn / Fail)")
    st.plotly_chart(_stacked_bar_chart(summary_df), use_container_width=True)

    # ── fail rate trend sparkline ──────────────────────────────────────────────
    if not summary_df["fail_rate_pct"].isna().all():
        st.subheader("Fail Rate Trend")
        chart_df = (
            summary_df.sort_values("started_at")
            .set_index("run_name")[["fail_rate_pct"]]
        )
        st.line_chart(chart_df, height=180)

    st.divider()

    # ── data table ────────────────────────────────────────────────────────────
    st.subheader("Run QC Summary Table")
    display_cols = [c for c in [
        "run_name", "started_at", "status", "assay_name",
        "instrument_name", "operator", "sample_count",
        "pass_count", "warn_count", "fail_count", "fail_rate_pct",
    ] if c in summary_df.columns]
    st.dataframe(summary_df[display_cols], use_container_width=True, hide_index=True)

    # ── drill-down link ───────────────────────────────────────────────────────
    st.subheader("Drill Down into a Run")
    run_ids = summary_df.sort_values("started_at", ascending=False)["run_id"].tolist()
    selected_run_id = st.selectbox(
        "Select run",
        options=run_ids,
        format_func=lambda rid: (
            summary_df[summary_df["run_id"] == rid]["run_name"].values[0]
            if rid in summary_df["run_id"].values else f"Run {rid}"
        ),
    )

    if st.button("Open in QC Investigation"):
        st.session_state["selected_run_id"] = int(selected_run_id)
        st.session_state["qc_mode"] = "By run"
        st.session_state["sidebar_page"] = "QC investigation"
        st.rerun()

    with st.expander("Advanced: SQL query reference"):
        st.markdown("**Run-level QC summary SQL (simplified)**")
        st.code(
            """
SELECT
  ar.run_id,
  ar.run_name,
  ar.started_at,
  a.name AS assay_name,
  COUNT(DISTINCT rs.run_sample_id) AS sample_count,
  SUM(CASE WHEN qr.result_flag = 'PASS' THEN 1 ELSE 0 END) AS pass_count,
  SUM(CASE WHEN qr.result_flag = 'WARN' THEN 1 ELSE 0 END) AS warn_count,
  SUM(CASE WHEN qr.result_flag = 'FAIL' THEN 1 ELSE 0 END) AS fail_count
FROM assay_run ar
JOIN assay a ON a.assay_id = ar.assay_id
LEFT JOIN run_sample rs ON rs.run_id = ar.run_id
LEFT JOIN qc_result qr ON qr.run_sample_id = rs.run_sample_id
WHERE ar.started_at BETWEEN :start AND :end
GROUP BY ar.run_id, ar.run_name, ar.started_at, a.name
ORDER BY ar.started_at DESC;
            """,
            language="sql",
        )
        plan_path = Path("explain/run_summary.txt")
        if plan_path.exists():
            st.markdown("**EXPLAIN ANALYZE plan**")
            st.text(plan_path.read_text())
        else:
            st.info("EXPLAIN plan file `explain/run_summary.txt` not generated yet.")
