-- Larger synthetic dataset to exercise indexes and generate meaningful EXPLAIN ANALYZE plans.
-- Uses generate_series to create hundreds of runs and thousands of samples.

-- Ensure core reference data exists (compatible with 02_seed_small.sql).
INSERT INTO assay (name, target_gene, description) VALUES
  ('SARS-CoV-2 N1', 'N1', 'Clinical respiratory panel targeting SARS-CoV-2 N1 gene')
ON CONFLICT (name) DO NOTHING;

INSERT INTO instrument (name, model, serial_number) VALUES
  ('qPCR-01', 'QuantStudio 5', 'QS5-0001')
ON CONFLICT (serial_number) DO NOTHING;


-- Create additional samples (approx. 5,000) if they do not already exist.
INSERT INTO sample (external_id, patient_id, collection_ts, matrix_type, notes)
SELECT
  'SMP-' || to_char(gs, 'FM000000') AS external_id,
  'PAT-' || to_char((gs % 2000) + 1, 'FM000000') AS patient_id,
  now() - (gs % 14) * interval '1 day' AS collection_ts,
  (ARRAY['Plasma','Serum','WholeBlood','CSF','Other'])[1 + (gs % 5)] AS matrix_type,
  'Synthetic bulk sample' AS notes
FROM generate_series(10001, 15000) AS gs
ON CONFLICT (external_id) DO NOTHING;


-- Create ~300 additional completed runs for the primary assay/instrument.
WITH
  a AS (
    SELECT assay_id FROM assay WHERE name = 'SARS-CoV-2 N1' LIMIT 1
  ),
  i AS (
    SELECT instrument_id FROM instrument WHERE name = 'qPCR-01' LIMIT 1
  )
INSERT INTO assay_run (assay_id, instrument_id, run_name, started_at, completed_at, status, operator)
SELECT
  a.assay_id,
  i.instrument_id,
  'BULK-N1-' || to_char(gs, 'FM0000') AS run_name,
  now() - (gs % 21) * interval '4 hours' AS started_at,
  now() - (gs % 21) * interval '4 hours' + interval '2 hours' AS completed_at,
  'COMPLETED' AS status,
  (ARRAY['alice','bob','carol','dave'])[1 + (gs % 4)] AS operator
FROM generate_series(1, 300) AS gs,
     a, i
ON CONFLICT (assay_id, run_name) DO NOTHING;


-- Attach ~96 samples per run, simulating a full 96-well plate.
-- We spread samples across the synthetic sample pool using modular arithmetic.
WITH
  runs AS (
    SELECT run_id, run_name
    FROM assay_run
    WHERE run_name LIKE 'BULK-N1-%'
  ),
  all_samples AS (
    SELECT sample_id
    FROM sample
    ORDER BY sample_id
  ),
  numbered_samples AS (
    SELECT sample_id,
           row_number() OVER (ORDER BY sample_id) AS rn
    FROM all_samples
  ),
  plate_positions AS (
    SELECT
      gs AS pos_index,
      chr(65 + ((gs - 1) / 12)) ||
      to_char(((gs - 1) % 12) + 1, 'FM00') AS well_position
    FROM generate_series(1, 96) AS gs
  )
INSERT INTO run_sample (run_id, sample_id, well_position, replicate_number, expected_ct, status)
SELECT
  r.run_id,
  s.sample_id,
  p.well_position,
  1 AS replicate_number,
  15.0 + (random() * 25.0) AS expected_ct, -- mostly within the 15-40 Ct window
  CASE
    WHEN random() < 0.05 THEN 'NO_AMPLIFICATION'
    WHEN random() < 0.08 THEN 'INVALID'
    ELSE 'AMPLIFIED'
  END AS status
FROM runs r
JOIN plate_positions p ON TRUE
JOIN numbered_samples s
  ON s.rn = ((r.run_id + p.pos_index) % (SELECT COUNT(*) FROM numbered_samples)) + 1
ON CONFLICT (run_id, sample_id, replicate_number) DO NOTHING;


-- Ensure QC rules exist (compatible with 02_seed_small.sql).
INSERT INTO qc_rule (name, applies_to, metric_name, operator, threshold_low, threshold_high, description) VALUES
  ('Ct_range_primary',       'SAMPLE', 'Ct',           'BETWEEN', 15.0, 35.0, 'Valid Ct window for clinical positives'),
  ('Ct_ntc_contamination',   'RUN',    'NTC_Ct',       '>',       35.0, NULL, 'No-template control must not amplify'),
  ('Replicate_sd_limit',     'SAMPLE', 'Replicate_SD', '<',       0.0,  1.0,  'Replicates must be tightly clustered'),
  ('Run_positive_control',   'RUN',    'PC_Ct',        'BETWEEN', 18.0, 25.0,'Positive control within expected Ct'),
  ('Run_failure_rate_high',  'RUN',    'Fail_Rate',    '<',       0.0,  0.10,'<10% failed samples per run')
ON CONFLICT (name) DO NOTHING;


-- Bulk sample-level QC results for Ct_range_primary and Replicate_sd_limit.
WITH
  rs AS (
    SELECT rs.run_sample_id,
           rs.run_id,
           rs.expected_ct
    FROM run_sample rs
    JOIN assay_run ar ON ar.run_id = rs.run_id
    WHERE ar.run_name LIKE 'BULK-N1-%'
  ),
  rules AS (
    SELECT qc_rule_id, name
    FROM qc_rule
    WHERE name IN ('Ct_range_primary', 'Replicate_sd_limit')
  )
INSERT INTO qc_result (run_id, run_sample_id, qc_rule_id, metric_name, metric_value, result_flag)
SELECT
  rs.run_id,
  rs.run_sample_id,
  r.qc_rule_id,
  CASE
    WHEN r.name = 'Ct_range_primary'   THEN 'Ct'
    WHEN r.name = 'Replicate_sd_limit' THEN 'Replicate_SD'
    ELSE r.name
  END AS metric_name,
  CASE
    WHEN r.name = 'Ct_range_primary' THEN rs.expected_ct
    WHEN r.name = 'Replicate_sd_limit' THEN 0.5 + (random() * 1.5)
    ELSE 0.0
  END AS metric_value,
  CASE
    WHEN r.name = 'Ct_range_primary' AND rs.expected_ct BETWEEN 15.0 AND 35.0 THEN 'PASS'
    WHEN r.name = 'Ct_range_primary' THEN 'FAIL'
    WHEN r.name = 'Replicate_sd_limit' AND (0.5 + (random() * 1.5)) < 1.0 THEN 'PASS'
    WHEN r.name = 'Replicate_sd_limit' THEN 'WARN'
    ELSE 'PASS'
  END AS result_flag
FROM rs
JOIN rules r ON TRUE
ON CONFLICT DO NOTHING;


-- Bulk run-level QC results for contamination, positive control, and failure rate.
WITH
  runs AS (
    SELECT run_id, run_name
    FROM assay_run
    WHERE run_name LIKE 'BULK-N1-%'
  ),
  rules AS (
    SELECT qc_rule_id, name
    FROM qc_rule
    WHERE name IN ('Ct_ntc_contamination', 'Run_positive_control', 'Run_failure_rate_high')
  ),
  failure_stats AS (
    SELECT
      rs.run_id,
      AVG(CASE WHEN rs.status IN ('NO_AMPLIFICATION','INVALID') THEN 1.0 ELSE 0.0 END) AS fail_rate
    FROM run_sample rs
    GROUP BY rs.run_id
  )
INSERT INTO qc_result (run_id, run_sample_id, qc_rule_id, metric_name, metric_value, result_flag)
SELECT
  r.run_id,
  NULL::BIGINT AS run_sample_id,
  q.qc_rule_id,
  CASE
    WHEN q.name = 'Ct_ntc_contamination'  THEN 'NTC_Ct'
    WHEN q.name = 'Run_positive_control'  THEN 'PC_Ct'
    WHEN q.name = 'Run_failure_rate_high' THEN 'Fail_Rate'
    ELSE q.name
  END AS metric_name,
  CASE
    WHEN q.name = 'Ct_ntc_contamination' THEN
      v.ntc_ct
    WHEN q.name = 'Run_positive_control' THEN
      v.pc_ct
    WHEN q.name = 'Run_failure_rate_high' THEN
      fs.fail_rate
    ELSE 0.0
  END AS metric_value,
  CASE
    WHEN q.name = 'Ct_ntc_contamination' AND v.ntc_ct <= 35.0 THEN 'FAIL'
    WHEN q.name = 'Run_failure_rate_high' AND fs.fail_rate > 0.10 THEN 'FAIL'
    WHEN q.name = 'Run_positive_control' THEN 'PASS'
    ELSE 'PASS'
  END AS result_flag
FROM runs r
JOIN rules q ON TRUE
LEFT JOIN failure_stats fs ON fs.run_id = r.run_id
CROSS JOIN LATERAL (
  SELECT
    -- 90% of runs have clean NTC; 10% show contamination (low Ct).
    CASE
      WHEN random() < 0.9 THEN 37.0 + (random() * 3.0)
      ELSE 25.0 + (random() * 3.0)
    END AS ntc_ct,
    19.0 + (random() * 3.0) AS pc_ct
) v
ON CONFLICT DO NOTHING;

