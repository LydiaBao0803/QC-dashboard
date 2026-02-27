-- Core schema for qPCR assay run & sample tracking + QC
-- Focus: clear FK/UNIQUE/CHECK constraints and indexes aligned to QC investigation queries.

-- Drop tables in dependency order for easy re-runs during development.
DO $$
BEGIN
  IF to_regclass('public.qc_result') IS NOT NULL THEN
    DROP TABLE qc_result;
  END IF;
  IF to_regclass('public.qc_rule') IS NOT NULL THEN
    DROP TABLE qc_rule;
  END IF;
  IF to_regclass('public.run_sample') IS NOT NULL THEN
    DROP TABLE run_sample;
  END IF;
  IF to_regclass('public.assay_run') IS NOT NULL THEN
    DROP TABLE assay_run;
  END IF;
  IF to_regclass('public.instrument') IS NOT NULL THEN
    DROP TABLE instrument;
  END IF;
  IF to_regclass('public.sample') IS NOT NULL THEN
    DROP TABLE sample;
  END IF;
  IF to_regclass('public.assay') IS NOT NULL THEN
    DROP TABLE assay;
  END IF;
END$$;


-- ================================================
-- Core reference data
-- ================================================

CREATE TABLE assay (
  assay_id       BIGSERIAL PRIMARY KEY,
  name           TEXT        NOT NULL,
  target_gene    TEXT        NOT NULL,
  description    TEXT,

  CONSTRAINT assay_name_unique UNIQUE (name)
);


CREATE TABLE sample (
  sample_id      BIGSERIAL PRIMARY KEY,
  external_id    TEXT        NOT NULL,
  patient_id     TEXT,
  collection_ts  TIMESTAMPTZ NOT NULL,
  matrix_type    TEXT        NOT NULL,
  notes          TEXT,

  CONSTRAINT sample_external_id_unique UNIQUE (external_id),
  CONSTRAINT sample_matrix_type_chk CHECK (matrix_type IN (
    'Plasma',
    'Serum',
    'WholeBlood',
    'CSF',
    'Other'
  ))
);


CREATE TABLE instrument (
  instrument_id  BIGSERIAL PRIMARY KEY,
  name           TEXT NOT NULL,
  model          TEXT NOT NULL,
  serial_number  TEXT NOT NULL,

  CONSTRAINT instrument_name_unique UNIQUE (name),
  CONSTRAINT instrument_serial_unique UNIQUE (serial_number)
);


-- ================================================
-- Runs and samples
-- ================================================

CREATE TABLE assay_run (
  run_id        BIGSERIAL PRIMARY KEY,
  assay_id      BIGINT      NOT NULL REFERENCES assay(assay_id),
  instrument_id BIGINT      NOT NULL REFERENCES instrument(instrument_id),
  run_name      TEXT        NOT NULL,
  started_at    TIMESTAMPTZ NOT NULL,
  completed_at  TIMESTAMPTZ,
  status        TEXT        NOT NULL,
  operator      TEXT        NOT NULL,

  CONSTRAINT assay_run_status_chk CHECK (status IN (
    'SCHEDULED',
    'RUNNING',
    'COMPLETED',
    'FAILED'
  )),

  -- Enforce uniqueness of run names per assay
  CONSTRAINT assay_run_name_unique UNIQUE (assay_id, run_name)
);


CREATE TABLE run_sample (
  run_sample_id    BIGSERIAL PRIMARY KEY,
  run_id           BIGINT      NOT NULL REFERENCES assay_run(run_id) ON DELETE CASCADE,
  sample_id        BIGINT      NOT NULL REFERENCES sample(sample_id),
  well_position    TEXT        NOT NULL,
  replicate_number INTEGER     NOT NULL DEFAULT 1,
  expected_ct      NUMERIC(6,3),
  status           TEXT        NOT NULL,

  CONSTRAINT run_sample_status_chk CHECK (status IN (
    'PENDING',
    'AMPLIFIED',
    'NO_AMPLIFICATION',
    'INVALID'
  )),

  -- A given sample can appear at most once per run/replicate
  CONSTRAINT run_sample_unique UNIQUE (run_id, sample_id, replicate_number),

  -- Enforce 96-well plate layout A01..H12 (adjust if you need 384)
  CONSTRAINT run_sample_well_position_chk
    CHECK (well_position ~ '^[A-H](0[1-9]|1[0-2])$')
);


-- ================================================
-- QC rules and results
-- ================================================

CREATE TABLE qc_rule (
  qc_rule_id    BIGSERIAL PRIMARY KEY,
  name          TEXT    NOT NULL,
  applies_to    TEXT    NOT NULL, -- 'RUN' or 'SAMPLE'
  metric_name   TEXT    NOT NULL, -- e.g. 'Ct', 'NTC_Ct', 'Replicate_SD'
  operator      TEXT    NOT NULL, -- '<', '>', 'BETWEEN', etc.
  threshold_low NUMERIC(10,3),
  threshold_high NUMERIC(10,3),
  description   TEXT,

  CONSTRAINT qc_rule_name_unique UNIQUE (name),
  CONSTRAINT qc_rule_applies_to_chk CHECK (applies_to IN ('RUN', 'SAMPLE')),
  CONSTRAINT qc_rule_operator_chk CHECK (operator IN ('<', '>', '=', '<=', '>=', 'BETWEEN'))
);


CREATE TABLE qc_result (
  qc_result_id  BIGSERIAL PRIMARY KEY,

  -- Every QC result is tied to a run; some are also tied to a specific sample (well).
  run_id        BIGINT      NOT NULL REFERENCES assay_run(run_id) ON DELETE CASCADE,
  run_sample_id BIGINT               REFERENCES run_sample(run_sample_id) ON DELETE CASCADE,
  qc_rule_id    BIGINT      NOT NULL REFERENCES qc_rule(qc_rule_id),

  metric_name   TEXT        NOT NULL,
  metric_value  NUMERIC(10,3) NOT NULL,
  result_flag   TEXT        NOT NULL, -- 'PASS', 'WARN', 'FAIL'
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT qc_result_flag_chk CHECK (result_flag IN ('PASS', 'WARN', 'FAIL'))
);


-- Uniqueness for QC results:
--  - For sample-level QC (run_sample_id IS NOT NULL), each (run_sample_id, qc_rule_id) should appear at most once.
--  - For run-level QC (run_sample_id IS NULL), each (run_id, qc_rule_id) should appear at most once.
CREATE UNIQUE INDEX uq_qc_result_sample_rule
  ON qc_result (run_sample_id, qc_rule_id)
  WHERE run_sample_id IS NOT NULL;

CREATE UNIQUE INDEX uq_qc_result_run_rule
  ON qc_result (run_id, qc_rule_id)
  WHERE run_sample_id IS NULL;


-- ================================================
-- Workload-driven indexes
-- ================================================

-- 1) Run-level QC overview: filter by status/date/assay and summarize by run.
CREATE INDEX idx_assay_run_status_started
  ON assay_run (status, started_at DESC);

CREATE INDEX idx_assay_run_assay_started
  ON assay_run (assay_id, started_at DESC);


-- 2) Sample lookup and lineage.
-- Uniqueness already enforced, but we rely on this for fast by-barcode lookup.
CREATE UNIQUE INDEX idx_sample_external_id
  ON sample (external_id);


-- 3) Run/sample joins in both directions.
CREATE INDEX idx_run_sample_run
  ON run_sample (run_id);

CREATE INDEX idx_run_sample_sample
  ON run_sample (sample_id);


-- 4) QC result access paths.
-- Join from run_sample into QC quickly (sample-level QC).
CREATE INDEX idx_qc_result_run_sample
  ON qc_result (run_sample_id);

-- Aggregate or filter QC results per run, grouped by flag and recency.
CREATE INDEX idx_qc_result_run_flag
  ON qc_result (run_id, result_flag, created_at DESC);

-- Drill into a specific QC metric and ranges (e.g., Ct outliers).
CREATE INDEX idx_qc_result_metric_flag
  ON qc_result (metric_name, result_flag, metric_value);

-- Fast failure-centric dashboards (only failed QC results).
CREATE INDEX idx_qc_result_failed_only
  ON qc_result (run_id, run_sample_id)
  WHERE result_flag = 'FAIL';

