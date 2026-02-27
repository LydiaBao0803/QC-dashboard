---

# Assay Run & Sample Tracking + QC Dashboard

- **Lydia Bao**
- Roche Assay Bioinformatics Summer Intern
- Feb 27, 2026

---

# Problem and Goals

- qPCR runs produce many samples and QC results.
- Need to:
  1. **Run-level QC summary** — which runs passed/failed, sample counts, failure rates.
  2. **Drill into one run** — per-sample QC (well, Ct, PASS/WARN/FAIL).
  3. **Trace a sample across runs** — QC history for a given sample ID.
- Goal: PostgreSQL + simple UI + evidence that queries are efficient (indexes + EXPLAIN ANALYZE).

---

# High-Level Architecture

- **PostgreSQL** — schema (tables, FKs, UNIQUE, CHECK) + workload-driven indexes.
- **Python** — psycopg; `app/queries.py` holds all SQL; no ORM.
- **Streamlit** — presentation only: `app/streamlit_app.py` + `app/pages/` (run_overview, qc_investigation).
- No Docker, no auth, no ORM.

```
PostgreSQL (schema + indexes)
  ↑
Python (psycopg, app/queries.py)
  ↑
Streamlit (app/streamlit_app.py, app/pages/)
```

---

# Schema Overview

- **assay** — Defines a qPCR assay (name, target gene, description).
- **instrument** — qPCR instruments (name, model, serial number).
- **sample** — Biological samples (external_id, patient_id, matrix_type, etc.).
- **assay_run** — One execution of an assay on an instrument (run_name, started_at, status, operator).
- **run_sample** — Which samples are in which run; well position, replicate, expected Ct, status.
- **qc_rule** — QC rule definitions (applies to RUN or SAMPLE; metric, operator, thresholds).
- **qc_result** — QC outcomes per run or per run_sample (metric_value, result_flag PASS/WARN/FAIL).

```
assay ──► assay_run ◄── instrument
              │
              ▼
         run_sample ◄── sample
              │
              ▼
         qc_result ◄── qc_rule
```

---

# Key Constraints

- **Foreign keys**: assay_run → assay, instrument; run_sample → assay_run, sample; qc_result → assay_run, run_sample (nullable), qc_rule.
- **UNIQUE**: sample.external_id; (assay_id, run_name) on assay_run; (run_id, sample_id, replicate_number) on run_sample; partial uniques on qc_result (sample-level and run-level).
- **CHECK**: assay_run.status (SCHEDULED/RUNNING/COMPLETED/FAILED); run_sample.status; sample.matrix_type; run_sample.well_position regex (A01–H12); qc_result.result_flag (PASS/WARN/FAIL).

---

# Indexing Strategy

- Principle: index for **query patterns**, not every column.
- **Run overview**: idx_assay_run_status_started, idx_run_sample_run, idx_qc_result_run_sample.
- **Drill-down by run**: idx_run_sample_run, idx_qc_result_run_sample.
- **Sample history**: idx_sample_external_id, idx_run_sample_sample, idx_qc_result_run_sample.
- **Partial index**: idx_qc_result_failed_only on (run_id, run_sample_id) WHERE result_flag = 'FAIL' for failure-centric dashboards.

---

# Query 1 — Run-Level Summary

- Filter runs by date range and status; join assay_run → assay → run_sample → qc_result; GROUP BY run; COUNT/SUM PASS/WARN/FAIL.
- Filters use status and started_at; indexes support that access path.

```sql
SELECT ar.run_id, ar.run_name, ar.started_at, a.name AS assay_name,
       COUNT(DISTINCT rs.run_sample_id) AS sample_count,
       SUM(CASE WHEN qr.result_flag = 'PASS' THEN 1 ELSE 0 END) AS pass_count,
       SUM(CASE WHEN qr.result_flag = 'WARN' THEN 1 ELSE 0 END) AS warn_count,
       SUM(CASE WHEN qr.result_flag = 'FAIL' THEN 1 ELSE 0 END) AS fail_count
FROM assay_run ar
JOIN assay a ON a.assay_id = ar.assay_id
LEFT JOIN run_sample rs ON rs.run_id = ar.run_id
LEFT JOIN qc_result qr ON qr.run_sample_id = rs.run_sample_id
WHERE ar.started_at BETWEEN :start AND :end AND ar.status = ANY(:statuses)
GROUP BY ar.run_id, ar.run_name, ar.started_at, a.name
ORDER BY ar.started_at DESC;
```

---

# Query 2 — Drill Into One Run

- idx_run_sample_run limits to one run’s wells; idx_qc_result_run_sample fetches QC per well.

```sql
SELECT rs.run_sample_id, s.external_id, rs.well_position, rs.replicate_number,
       rs.expected_ct, qr.metric_name, qr.metric_value, qr.result_flag
FROM run_sample rs
JOIN sample s ON s.sample_id = rs.sample_id
LEFT JOIN qc_result qr ON qr.run_sample_id = rs.run_sample_id
WHERE rs.run_id = :run_id
ORDER BY rs.well_position, rs.replicate_number, qr.metric_name;
```

---

# Query 3 — Sample QC History

- Lookup by external_id then join by sample_id; indexes support both.

```sql
SELECT s.external_id, ar.run_name, ar.started_at, rs.well_position,
       qr.metric_name, qr.metric_value, qr.result_flag
FROM sample s
JOIN run_sample rs ON rs.sample_id = s.sample_id
JOIN assay_run ar ON ar.run_id = rs.run_id
LEFT JOIN qc_result qr ON qr.run_sample_id = rs.run_sample_id
WHERE s.external_id = :external_id
ORDER BY ar.started_at DESC;
```

---

# UI Walkthrough

- **Run overview** (`app/pages/run_overview.py`): Sidebar filters (date, status, assay) → table with run-level QC + fail rate chart → “Open in QC investigation” to jump to a run.
- **QC investigation** (`app/pages/qc_investigation.py`): Two modes —
  - **By run**: run_id → run metadata + per-sample QC table.
  - **By sample**: external_id → history table across runs.
- Thin presentation layer: pages call `app/queries.py` and display DataFrames/tables/charts.

---

# Performance Evidence (EXPLAIN ANALYZE)

- **Drill-down query**: Index Scan on idx_run_sample_run (rows ≈ 96), Index Scan on idx_qc_result_run_sample; Execution Time ~1–2 ms. Both indexes used.
- **Run summary**: At current scale, planner may use Seq Scan on qc_result/run_sample with hash joins; acceptable for demo data. For much larger data, consider partitioning or additional indexes.

```
Index Scan using idx_run_sample_run on run_sample rs
  Index Cond: (run_id = 123)
  Rows: 96
  -> Index Scan using idx_qc_result_run_sample on qc_result qr
Execution Time: 1.7 ms
```

---

# Summary and Takeaways

- Schema enforces integrity (FKs, UNIQUE, CHECK).
- Indexes are aligned to three main query patterns (run summary, run drill-down, sample history).
- Streamlit is presentation-only; SQL lives in `app/queries.py`.
- EXPLAIN ANALYZE confirms index usage and sub-second response for drill-down.

---

# Thank You / Q&A

