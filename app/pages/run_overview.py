from datetime import date, timedelta
from pathlib import Path
from typing import Iterable, List, Optional

import pandas as pd
import streamlit as st

from app.db import fetch_df
from app.queries import get_run_qc_summary


def _assay_selector() -> Optional[int]:
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


def render() -> None:
    st.header("Run overview")

    today = date.today()
    default_start = today - timedelta(days=7)
    start_date, end_date = st.sidebar.date_input(
        "Run date range",
        (default_start, today),
        help="Filter runs by start timestamp (inclusive).",
    )

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
        st.info("No runs found for the selected filters.")
        return

    st.subheader("Run QC summary")
    display_df = summary_df.copy()
    if "fail_rate" in display_df.columns:
        display_df["fail_rate"] = pd.to_numeric(display_df["fail_rate"], errors="coerce")
        display_df["fail_rate_pct"] = (display_df["fail_rate"] * 100).round(1)
    st.dataframe(display_df, use_container_width=True)

    if "fail_rate" in summary_df.columns:
        summary_df["fail_rate"] = pd.to_numeric(summary_df["fail_rate"], errors="coerce")
    if "fail_rate" in summary_df.columns and not summary_df["fail_rate"].isna().all():
        st.subheader("Failure rate by run")
        chart_df = summary_df[["run_name", "fail_rate"]].copy()
        chart_df["fail_rate_pct"] = (chart_df["fail_rate"] * 100).round(1)
        chart_df = chart_df.set_index("run_name")[["fail_rate_pct"]]
        st.bar_chart(chart_df)

    st.subheader("Inspect a run in detail")
    run_ids = summary_df.sort_values("started_at", ascending=False)["run_id"].tolist()
    selected_run_id = st.selectbox(
        "Run",
        options=run_ids,
        format_func=lambda rid: f"Run {rid}",
    )

    if st.button("Open in QC investigation"):
        st.session_state["selected_run_id"] = int(selected_run_id)
        st.session_state["qc_mode"] = "By run"
        st.rerun()

    with st.expander("Advanced: SQL and EXPLAIN plan"):
        st.markdown("**Run-level QC summary SQL (simplified view)**")
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

