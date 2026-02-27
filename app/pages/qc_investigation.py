from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st

from app.db import fetch_df
from app.queries import get_run_qc_details, get_sample_qc_history


def _load_run_metadata(run_id: int) -> pd.DataFrame:
    sql = """
        SELECT
            ar.run_id,
            ar.run_name,
            ar.started_at,
            ar.completed_at,
            ar.status,
            a.name AS assay_name,
            i.name AS instrument_name,
            ar.operator
        FROM assay_run ar
        JOIN assay a ON a.assay_id = ar.assay_id
        JOIN instrument i ON i.instrument_id = ar.instrument_id
        WHERE ar.run_id = %s
    """
    return fetch_df(sql, [run_id])


def _render_by_run() -> None:
    st.subheader("Investigate by run")

    default_run_id = st.session_state.get("selected_run_id", 1)
    run_id = st.number_input(
        "Run ID",
        min_value=1,
        step=1,
        value=int(default_run_id),
    )

    if st.button("Load run"):
        st.session_state["selected_run_id"] = int(run_id)

    run_id = int(st.session_state.get("selected_run_id", run_id))

    meta_df = _load_run_metadata(run_id)
    if meta_df.empty:
        st.info(f"No run found with run_id = {run_id}.")
        return

    st.markdown("**Run metadata**")
    st.table(meta_df)

    qc_df = get_run_qc_details(run_id)
    if qc_df.empty:
        st.info("No QC results found for this run.")
        return

    st.markdown("**Per-sample QC details**")
    st.dataframe(qc_df, use_container_width=True)

    with st.expander("Advanced: SQL and EXPLAIN plan"):
        st.markdown("**Run drill-down SQL (simplified view)**")
        st.code(
            """
SELECT
  rs.run_sample_id,
  s.external_id,
  rs.well_position,
  rs.replicate_number,
  rs.expected_ct,
  qr.metric_name,
  qr.metric_value,
  qr.result_flag
FROM run_sample rs
JOIN sample s ON s.sample_id = rs.sample_id
LEFT JOIN qc_result qr
  ON qr.run_sample_id = rs.run_sample_id
WHERE rs.run_id = :run_id
ORDER BY rs.well_position, rs.replicate_number, qr.metric_name;
            """,
            language="sql",
        )

        plan_path = Path("explain/run_drilldown.txt")
        if plan_path.exists():
            st.markdown("**EXPLAIN ANALYZE plan**")
            st.text(plan_path.read_text())
        else:
            st.info("EXPLAIN plan file `explain/run_drilldown.txt` not generated yet.")


def _render_by_sample() -> None:
    st.subheader("Investigate by sample")

    default_external_id: Optional[str] = st.session_state.get("selected_sample_external_id")
    external_id = st.text_input(
        "Sample external ID",
        value=default_external_id or "",
        placeholder="e.g., SMP-0001",
    )

    if not external_id:
        st.info("Enter a sample external ID to view its QC history.")
        return

    if st.button("Load sample history"):
        st.session_state["selected_sample_external_id"] = external_id

    external_id = st.session_state.get("selected_sample_external_id", external_id)

    history_df = get_sample_qc_history(external_id)
    if history_df.empty:
        st.info(f"No QC history found for sample `{external_id}`.")
        return

    st.markdown(f"**QC history for sample `{external_id}`**")
    st.dataframe(history_df, use_container_width=True)

    with st.expander("Advanced: SQL and EXPLAIN plan"):
        st.markdown("**Sample QC history SQL (simplified view)**")
        st.code(
            """
SELECT
  s.external_id,
  ar.run_name,
  ar.started_at,
  rs.well_position,
  rs.replicate_number,
  qr.metric_name,
  qr.metric_value,
  qr.result_flag
FROM sample s
JOIN run_sample rs ON rs.sample_id = s.sample_id
JOIN assay_run ar ON ar.run_id = rs.run_id
LEFT JOIN qc_result qr
  ON qr.run_sample_id = rs.run_sample_id
WHERE s.external_id = :external_id
ORDER BY ar.started_at DESC, ar.run_id, qr.metric_name;
            """,
            language="sql",
        )

        plan_path = Path("explain/sample_history.txt")
        if plan_path.exists():
            st.markdown("**EXPLAIN ANALYZE plan**")
            st.text(plan_path.read_text())
        else:
            st.info("EXPLAIN plan file `explain/sample_history.txt` not generated yet.")


def render() -> None:
    st.header("QC investigation")

    mode = st.radio(
        "Mode",
        options=["By run", "By sample"],
        index=0 if st.session_state.get("qc_mode", "By run") == "By run" else 1,
    )
    st.session_state["qc_mode"] = mode

    if mode == "By run":
        _render_by_run()
    else:
        _render_by_sample()

