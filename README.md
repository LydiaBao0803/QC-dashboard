# Assay Run & Sample Tracking + QC Dashboard (qPCR demo)

Minimal but realistic qPCR assay run and sample tracking demo built for PostgreSQL + Python + Streamlit.  
Focus areas:

- **Schema design**: clear FKs, UNIQUE and CHECK constraints.
- **Indexing strategy**: tuned to specific QC investigation query patterns.
- **Simple UI architecture**: Streamlit as the presentation layer only.
- **Performance evidence**: `EXPLAIN (ANALYZE, BUFFERS, VERBOSE)` for key queries.


## 1. Prerequisites

- macOS with **Python 3.10+**.
- Local **PostgreSQL** instance (no Docker), managed via `pgAdmin` or `psql`.
- A PostgreSQL role that can create databases, tables, and indexes.


## 2. Database setup

### 2.1. Create database

From `psql` or pgAdmin, create a database (default name used by the app is `assay_qc`):

```sql
CREATE DATABASE assay_qc;
```

If you use a different name, update `DB_NAME` when running the app (see environment variables below).


### 2.2. Apply schema and seed data

In this repo root:

```bash
psql -d assay_qc -f schema/01_schema.sql
psql -d assay_qc -f schema/02_seed_small.sql
psql -d assay_qc -f schema/03_seed_perf.sql
```

- `01_schema.sql` – core tables, FKs, UNIQUE and CHECK constraints, and workload-driven indexes.
- `02_seed_small.sql` – human-readable sample data for a few runs, samples, and QC rules/results.
- `03_seed_perf.sql` – larger synthetic dataset (hundreds of runs, thousands of samples) for performance testing.


## 3. Python environment

From the repo root:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```


## 4. Configuration (environment variables)

The app reads database settings from environment variables, with sensible defaults:

- `DB_HOST` (default: `localhost`)
- `DB_PORT` (default: `5432`)
- `DB_NAME` (default: `assay_qc`)
- `DB_USER` (default: `postgres`)
- `DB_PASSWORD` (default: empty)

Example:

```bash
export DB_HOST=localhost
export DB_PORT=5432
export DB_NAME=assay_qc
export DB_USER=postgres
export DB_PASSWORD=your_password
```


## 5. Running the Streamlit UI

From the repo root:

```bash
streamlit run app.py
```

`app.py` delegates to `app/streamlit_app.py`, which uses:

- `app/config.py` – loads DB configuration from environment variables.
- `app/db.py` – cached connection (`st.cache_resource`) and small query helpers.
- `app/queries.py` – core QC investigation queries.
- `app/pages/run_overview.py` – run-level QC summary dashboard.
- `app/pages/qc_investigation.py` – drill-down views by run and by sample.


### 5.1. Run Overview page

This page:

- Filters runs by **date range**, **status**, and **assay**.
- Shows run-level QC summary (PASS/WARN/FAIL counts, sample counts, failure rates).
- Provides a small **failure-rate bar chart** by run.
- Lets you select a run and jump directly to the QC investigation page.

Key query pattern (simplified):

```sql
SELECT
  ar.run_id,
  ar.run_name,
  ar.started_at,
  a.name AS assay_name,
  COUNT(DISTINCT rs.run_sample_id) AS sample_count,
  SUM(CASE WHEN qr.result_flag = 'FAIL' THEN 1 ELSE 0 END) AS fail_count
FROM assay_run ar
JOIN assay a ON a.assay_id = ar.assay_id
LEFT JOIN run_sample rs ON rs.run_id = ar.run_id
LEFT JOIN qc_result qr ON qr.run_sample_id = rs.run_sample_id
WHERE ar.started_at BETWEEN :start AND :end
GROUP BY ar.run_id, ar.run_name, ar.started_at, a.name;
```

Tied indexes:

- `idx_assay_run_status_started` on `(status, started_at DESC)`
- `idx_assay_run_assay_started` on `(assay_id, started_at DESC)`
- `idx_run_sample_run` on `(run_id)`
- `idx_qc_result_run_sample` on `(run_sample_id)`


### 5.2. QC Investigation page

Two modes:

- **By run**:
  - Enter or receive a run ID (via navigation from the overview).
  - View run metadata (assay, instrument, operator, timestamps).
  - See per-sample QC details for that run.

  Query shape:

  ```sql
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
  WHERE rs.run_id = :run_id;
  ```

  Index usage:

  - `idx_run_sample_run` on `(run_id)` to find samples for a run.
  - `idx_qc_result_run_sample` on `(run_sample_id)` for fast QC joins.

- **By sample**:
  - Input a `sample.external_id` (e.g., `SMP-0001`).
  - View that sample’s history across runs/assays with QC metrics and flags.

  Query shape:

  ```sql
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
  ORDER BY ar.started_at DESC;
  ```

  Index usage:

  - `idx_sample_external_id` UNIQUE(`external_id`) for direct sample lookup.
  - `idx_run_sample_sample` on `(sample_id)` to find all runs for a sample.
  - `idx_qc_result_run_sample` on `(run_sample_id)` for QC joins.


## 6. Schema and constraints (talk track)

Core tables (see `schema/01_schema.sql`):

- `assay` – defines a qPCR assay (unique `name`, target gene, description).
- `sample` – biological samples (unique `external_id`, `matrix_type` CHECK constraint).
- `instrument` – qPCR instruments (unique `name`, unique `serial_number`).
- `assay_run` – a single execution of an assay on an instrument.
  - FK to `assay` and `instrument`.
  - `status` CHECK constraint (`SCHEDULED`/`RUNNING`/`COMPLETED`/`FAILED`).
  - UNIQUE(`assay_id`, `run_name`) to prevent duplicates.
- `run_sample` – specific samples within a run (one run, many samples).
  - FK to `assay_run` and `sample`.
  - UNIQUE(`run_id`, `sample_id`, `replicate_number`).
  - `well_position` CHECK enforcing a 96-well layout (A01..H12).
- `qc_rule` – QC rules that apply to runs or samples.
  - UNIQUE(`name`).
  - CHECKs on `applies_to` and `operator`.
- `qc_result` – QC outcomes:
  - FK to `assay_run`, optional FK to `run_sample`, FK to `qc_rule`.
  - `result_flag` CHECK (`PASS`/`WARN`/`FAIL`).
  - Partial UNIQUE indexes:
    - `(run_sample_id, qc_rule_id)` where `run_sample_id IS NOT NULL`.
    - `(run_id, qc_rule_id)` where `run_sample_id IS NULL`.

High-level explanation:

- FKs ensure referential integrity across assays, runs, samples, and QC rules/results.
- UNIQUE + partial UNIQUE constraints prevent duplicate QC entries without over-normalizing.
- CHECK constraints model domain enums directly in the schema.


## 7. Running EXPLAIN ANALYZE for evidence

The script `scripts/run_explain.py` runs `EXPLAIN (ANALYZE, BUFFERS, VERBOSE)` against three representative queries and writes output to the `explain/` directory:

- `explain/run_summary.txt` – run-level QC summary.
- `explain/run_drilldown.txt` – per-run QC drill-down.
- `explain/sample_history.txt` – sample QC history.

Run it from the repo root:

```bash
python scripts/run_explain.py
```

In the Streamlit UI, each page has an **Advanced** expander that:

- Shows a **simplified SQL** version for the main query.
- If present, loads the matching `EXPLAIN` output file so you can walk through the plan live.

Suggested talking points:

- Point out how the planner uses the composite and partial indexes.
- Highlight row estimates vs. actuals, and how that changes once indexes exist.
- Note that the queries avoid `SELECT *`, use parameter binding, and match index column order.


   - PostgreSQL + psycopg + Streamlit only.
   - No authentication, no complex front-end framework—just enough to clearly demonstrate data modeling, query design, and performance thinking.

