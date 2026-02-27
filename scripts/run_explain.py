from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Sequence

import psycopg

from app.config import DbConfig, load_db_config


def get_connection() -> psycopg.Connection:
    cfg: DbConfig = load_db_config()
    return psycopg.connect(
        host=cfg.host,
        port=cfg.port,
        dbname=cfg.dbname,
        user=cfg.user,
        password=cfg.password,
    )


def run_explain(
    conn: psycopg.Connection,
    label: str,
    sql: str,
    params: Sequence[object],
    output_dir: Path,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{label}.txt"

    explain_sql = "EXPLAIN (ANALYZE, BUFFERS, VERBOSE) " + sql

    with conn.cursor() as cur:
        cur.execute(explain_sql, params)
        rows = cur.fetchall()

    plan_lines = "\n".join(str(r[0]) for r in rows)
    header = f"-- Label: {label}\n-- Generated at: {datetime.utcnow().isoformat()}Z\n"
    path.write_text(header + "\n" + plan_lines)
    print(f"Wrote EXPLAIN output for {label!r} to {path}")


def pick_example_run(conn: psycopg.Connection) -> int | None:
    sql = """
        SELECT run_id
        FROM assay_run
        ORDER BY started_at DESC
        LIMIT 1
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        row = cur.fetchone()
    return int(row[0]) if row else None


def pick_example_sample_external_id(conn: psycopg.Connection) -> str | None:
    sql = """
        SELECT s.external_id
        FROM sample s
        JOIN run_sample rs ON rs.sample_id = s.sample_id
        ORDER BY rs.run_id DESC
        LIMIT 1
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        row = cur.fetchone()
    return str(row[0]) if row else None


def main() -> None:
    conn = get_connection()
    output_dir = Path(__file__).resolve().parents[1] / "explain"

    # 1) Run-level QC summary over the last 7 days.
    now = datetime.utcnow()
    start_dt = now - timedelta(days=7)
    end_dt = now + timedelta(days=1)
    statuses: Iterable[str] = ["COMPLETED", "FAILED"]

    run_summary_sql = """
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
        WHERE ar.started_at >= %s
          AND ar.started_at < %s
          AND ar.status = ANY(%s)
        GROUP BY
            ar.run_id,
            ar.run_name,
            ar.started_at,
            ar.completed_at,
            ar.status,
            a.name,
            i.name
        ORDER BY ar.started_at DESC
        LIMIT 200
    """
    run_explain(
        conn,
        label="run_summary",
        sql=run_summary_sql,
        params=[start_dt, end_dt, list(statuses)],
        output_dir=output_dir,
    )

    # 2) Drill into a single example run.
    example_run_id = pick_example_run(conn)
    if example_run_id is not None:
        run_details_sql = """
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
        run_explain(
            conn,
            label="run_drilldown",
            sql=run_details_sql,
            params=[example_run_id],
            output_dir=output_dir,
        )
    else:
        print("No runs found; skipping run_drilldown EXPLAIN.")

    # 3) Sample QC history for an example sample.
    example_external_id = pick_example_sample_external_id(conn)
    if example_external_id is not None:
        sample_history_sql = """
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
        run_explain(
            conn,
            label="sample_history",
            sql=sample_history_sql,
            params=[example_external_id],
            output_dir=output_dir,
        )
    else:
        print("No samples with runs found; skipping sample_history EXPLAIN.")

    conn.close()


if __name__ == "__main__":
    main()

