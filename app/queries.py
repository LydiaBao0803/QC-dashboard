from datetime import date, datetime, timedelta
from typing import Iterable, Optional, Sequence

import pandas as pd

from .db import fetch_df, is_demo_mode


def get_run_qc_summary(
    start_date: Optional[date],
    end_date: Optional[date],
    assay_id: Optional[int] = None,
    statuses: Optional[Iterable[str]] = None,
    limit: int = 200,
) -> pd.DataFrame:
    """Aggregate QC flags per run over a date range, optionally filtered by assay and status."""
    if is_demo_mode():
        from .demo_data import get_run_qc_summary_demo
        return get_run_qc_summary_demo(start_date, end_date, assay_id, statuses, limit)

    sql = """
        SELECT
            ar.run_id,
            ar.run_name,
            ar.started_at,
            ar.completed_at,
            ar.status,
            a.name AS assay_name,
            i.name AS instrument_name,
            COUNT(DISTINCT rs.run_sample_id) AS sample_count,
            COALESCE(SUM(CASE WHEN qr.result_flag = 'PASS' THEN 1 ELSE 0 END), 0) AS pass_count,
            COALESCE(SUM(CASE WHEN qr.result_flag = 'WARN' THEN 1 ELSE 0 END), 0) AS warn_count,
            COALESCE(SUM(CASE WHEN qr.result_flag = 'FAIL' THEN 1 ELSE 0 END), 0) AS fail_count
        FROM assay_run ar
        JOIN assay a ON a.assay_id = ar.assay_id
        JOIN instrument i ON i.instrument_id = ar.instrument_id
        LEFT JOIN run_sample rs ON rs.run_id = ar.run_id
        LEFT JOIN qc_result qr ON qr.run_sample_id = rs.run_sample_id
        WHERE 1 = 1
    """
    params: list[object] = []

    if start_date:
        start_dt = datetime.combine(start_date, datetime.min.time())
        sql += " AND ar.started_at >= %s"
        params.append(start_dt)

    if end_date:
        end_dt = datetime.combine(end_date + timedelta(days=1), datetime.min.time())
        sql += " AND ar.started_at < %s"
        params.append(end_dt)

    if assay_id is not None:
        sql += " AND ar.assay_id = %s"
        params.append(assay_id)

    if statuses:
        status_list = list(statuses)
        sql += " AND ar.status = ANY(%s)"
        params.append(status_list)

    sql += """
        GROUP BY
            ar.run_id,
            ar.run_name,
            ar.started_at,
            ar.completed_at,
            ar.status,
            a.name,
            i.name
        ORDER BY ar.started_at DESC
        LIMIT %s
    """
    params.append(limit)

    df = fetch_df(sql, params)
    if not df.empty:
        total_qc = df[["pass_count", "warn_count", "fail_count"]].sum(axis=1)
        # Avoid division by zero: when total_qc is 0, treat fail_rate as NaN.
        total_qc = total_qc.replace({0: pd.NA})
        df["fail_rate"] = df["fail_count"] / total_qc
        # Ensure numeric dtype for downstream calculations/plots.
        df["fail_rate"] = pd.to_numeric(df["fail_rate"], errors="coerce")
    return df


def get_run_qc_details(run_id: int) -> pd.DataFrame:
    """Return per-sample QC metrics for a specific run."""
    if is_demo_mode():
        from .demo_data import get_run_qc_details_demo
        return get_run_qc_details_demo(run_id)

    sql = """
        SELECT
            rs.run_sample_id,
            s.external_id,
            s.patient_id,
            rs.well_position,
            rs.replicate_number,
            rs.expected_ct,
            rs.status AS assay_status,
            qr.metric_name,
            qr.metric_value,
            qr.result_flag
        FROM run_sample rs
        JOIN sample s ON s.sample_id = rs.sample_id
        LEFT JOIN qc_result qr
            ON qr.run_sample_id = rs.run_sample_id
        WHERE rs.run_id = %s
        ORDER BY rs.well_position, rs.replicate_number, qr.metric_name
    """
    return fetch_df(sql, [run_id])


def get_sample_qc_history(external_id: str) -> pd.DataFrame:
    """Return QC history for a given sample across all runs/assays."""
    if is_demo_mode():
        from .demo_data import get_sample_qc_history_demo
        return get_sample_qc_history_demo(external_id)

    sql = """
        SELECT
            s.external_id,
            ar.run_id,
            ar.run_name,
            ar.started_at,
            ar.status,
            a.name AS assay_name,
            i.name AS instrument_name,
            rs.well_position,
            rs.replicate_number,
            rs.expected_ct,
            qr.metric_name,
            qr.metric_value,
            qr.result_flag
        FROM sample s
        JOIN run_sample rs ON rs.sample_id = s.sample_id
        JOIN assay_run ar ON ar.run_id = rs.run_id
        JOIN assay a ON a.assay_id = ar.assay_id
        JOIN instrument i ON i.instrument_id = ar.instrument_id
        LEFT JOIN qc_result qr
            ON qr.run_sample_id = rs.run_sample_id
        WHERE s.external_id = %s
        ORDER BY ar.started_at DESC, ar.run_id, qr.metric_name
    """
    return fetch_df(sql, [external_id])

