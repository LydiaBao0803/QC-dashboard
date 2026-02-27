-- Small, human-readable seed data for the qPCR assay QC demo.
-- Intended for manual inspection in pgAdmin and quick UI smoke tests.

-- Assays
INSERT INTO assay (name, target_gene, description) VALUES
  ('SARS-CoV-2 N1', 'N1', 'Clinical respiratory panel targeting SARS-CoV-2 N1 gene'),
  ('Influenza A M1', 'M1', 'Seasonal influenza A M1 gene assay')
ON CONFLICT (name) DO NOTHING;


-- Instruments
INSERT INTO instrument (name, model, serial_number) VALUES
  ('qPCR-01', 'QuantStudio 5', 'QS5-0001'),
  ('qPCR-02', 'QuantStudio 7', 'QS7-0002')
ON CONFLICT (serial_number) DO NOTHING;


-- Samples
INSERT INTO sample (external_id, patient_id, collection_ts, matrix_type, notes) VALUES
  ('SMP-0001', 'PAT-001', now() - interval '2 days', 'Plasma',      'Baseline sample'),
  ('SMP-0002', 'PAT-001', now() - interval '1 days', 'Plasma',      'Follow-up'),
  ('SMP-0003', 'PAT-002', now() - interval '3 days', 'Serum',       'Febrile visit'),
  ('SMP-0004', 'PAT-003', now() - interval '4 days', 'WholeBlood',  'ICU admission'),
  ('SMP-0005', 'PAT-004', now() - interval '2 days', 'Plasma',      'Outpatient'),
  ('SMP-0006', 'PAT-005', now() - interval '5 days', 'Serum',       'Screening'),
  ('SMP-0007', 'PAT-006', now() - interval '1 days', 'Plasma',      'High viral load expected'),
  ('SMP-0008', 'PAT-007', now() - interval '6 days', 'CSF',         'Neurologic symptoms'),
  ('SMP-0009', 'PAT-008', now() - interval '3 days', 'Plasma',      'Asymptomatic contact'),
  ('SMP-0010','PAT-009', now() - interval '1 days', 'Serum',        'Repeat test')
ON CONFLICT (external_id) DO NOTHING;


-- QC rules
-- Simple, interpretable rules for Ct values and replicate variability.
INSERT INTO qc_rule (name, applies_to, metric_name, operator, threshold_low, threshold_high, description) VALUES
  ('Ct_range_primary',       'SAMPLE', 'Ct',           'BETWEEN', 15.0, 35.0, 'Valid Ct window for clinical positives'),
  ('Ct_ntc_contamination',   'RUN',    'NTC_Ct',       '>',       35.0, NULL, 'No-template control must not amplify'),
  ('Replicate_sd_limit',     'SAMPLE', 'Replicate_SD', '<',       0.0,  1.0,  'Replicates must be tightly clustered'),
  ('Run_positive_control',   'RUN',    'PC_Ct',        'BETWEEN', 18.0, 25.0,'Positive control within expected Ct'),
  ('Run_failure_rate_high',  'RUN',    'Fail_Rate',    '<',       0.0,  0.10,'<10% failed samples per run')
ON CONFLICT (name) DO NOTHING;


-- Helper CTEs to reference inserted keys by business identifiers
WITH a AS (
  SELECT assay_id, name
  FROM assay
  WHERE name IN ('SARS-CoV-2 N1', 'Influenza A M1')
),
i AS (
  SELECT instrument_id, name
  FROM instrument
  WHERE name IN ('qPCR-01', 'qPCR-02')
)
-- Assay runs
INSERT INTO assay_run (assay_id, instrument_id, run_name, started_at, completed_at, status, operator)
SELECT
  (SELECT assay_id FROM a WHERE name = 'SARS-CoV-2 N1')  AS assay_id,
  (SELECT instrument_id FROM i WHERE name = 'qPCR-01')   AS instrument_id,
  v.run_name,
  v.started_at,
  v.completed_at,
  v.status,
  v.operator
FROM (
  VALUES
    ('RUN-N1-001', now() - interval '1 days' - interval '3 hours', now() - interval '1 days' - interval '1 hours', 'COMPLETED', 'alice'),
    ('RUN-N1-002', now() - interval '12 hours',                     now() - interval '10 hours',                    'COMPLETED', 'bob'),
    ('RUN-N1-003', now() - interval '3 hours',                      NULL,                                           'RUNNING',   'alice'),
    ('RUN-M1-001', now() - interval '2 days',                       now() - interval '2 days' + interval '2 hours','COMPLETED', 'carol')
) AS v(run_name, started_at, completed_at, status, operator)
ON CONFLICT (assay_id, run_name) DO NOTHING;


-- Link samples into runs with well positions and expected Ct values.
-- We focus on two completed runs (N1-001, N1-002) for QC.
WITH
  runs AS (
    SELECT run_id, run_name
    FROM assay_run
    WHERE run_name IN ('RUN-N1-001', 'RUN-N1-002')
  ),
  smp AS (
    SELECT sample_id, external_id
    FROM sample
    WHERE external_id BETWEEN 'SMP-0001' AND 'SMP-0010'
  )
INSERT INTO run_sample (run_id, sample_id, well_position, replicate_number, expected_ct, status)
SELECT
  r.run_id,
  s.sample_id,
  v.well_position,
  v.replicate_number,
  v.expected_ct,
  v.status
FROM runs r
JOIN smp s
  ON TRUE
JOIN (
  VALUES
    -- RUN-N1-001 layout (8 samples x 1 replicate, some failures)
    ('RUN-N1-001','SMP-0001','A01',1, 25.3,'AMPLIFIED'),
    ('RUN-N1-001','SMP-0002','A02',1, 26.1,'AMPLIFIED'),
    ('RUN-N1-001','SMP-0003','A03',1, 36.5,'NO_AMPLIFICATION'),  -- expected negative / out-of-range
    ('RUN-N1-001','SMP-0004','A04',1, 14.2,'INVALID'),          -- Ct too low, likely control issue
    ('RUN-N1-001','SMP-0005','A05',1, 28.0,'AMPLIFIED'),
    ('RUN-N1-001','SMP-0006','A06',1, 33.7,'AMPLIFIED'),
    ('RUN-N1-001','SMP-0007','A07',1, 19.5,'AMPLIFIED'),
    ('RUN-N1-001','SMP-0008','A08',1, 21.2,'AMPLIFIED'),

    -- RUN-N1-002 layout (same samples, slightly different Ct)
    ('RUN-N1-002','SMP-0001','A01',1, 24.8,'AMPLIFIED'),
    ('RUN-N1-002','SMP-0002','A02',1, 25.7,'AMPLIFIED'),
    ('RUN-N1-002','SMP-0003','A03',1, 35.7,'NO_AMPLIFICATION'),
    ('RUN-N1-002','SMP-0004','A04',1, 15.1,'INVALID'),
    ('RUN-N1-002','SMP-0005','A05',1, 27.3,'AMPLIFIED'),
    ('RUN-N1-002','SMP-0006','A06',1, 34.5,'AMPLIFIED'),
    ('RUN-N1-002','SMP-0007','A07',1, 20.3,'AMPLIFIED'),
    ('RUN-N1-002','SMP-0008','A08',1, 22.0,'AMPLIFIED')
) AS v(run_name, external_id, well_position, replicate_number, expected_ct, status)
  ON v.run_name = r.run_name
 AND v.external_id = s.external_id
ON CONFLICT (run_id, sample_id, replicate_number) DO NOTHING;


-- Sample-level QC results: Ct_range_primary and Replicate_sd_limit per run_sample
WITH
  rs AS (
    SELECT rs.run_sample_id,
           rs.run_id,
           s.external_id,
           rs.expected_ct
    FROM run_sample rs
    JOIN sample s ON s.sample_id = rs.sample_id
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
  r.name = 'Ct_range_primary'    ::TEXT AS metric_name_dummy, -- placeholder, overwritten below
  rs.expected_ct,
  CASE
    WHEN r.name = 'Ct_range_primary' AND rs.expected_ct BETWEEN 15.0 AND 35.0 THEN 'PASS'
    WHEN r.name = 'Ct_range_primary' THEN 'FAIL'
    WHEN r.name = 'Replicate_sd_limit' THEN
      CASE
        WHEN rs.external_id IN ('SMP-0001','SMP-0002','SMP-0005','SMP-0007') THEN 'PASS'
        ELSE 'WARN'
      END
    ELSE 'PASS'
  END AS result_flag
FROM rs
JOIN rules r ON TRUE
ON CONFLICT DO NOTHING;

-- Overwrite metric_name to match the rule's intended metric for clarity.
UPDATE qc_result qr
SET metric_name = CASE
  WHEN qr.qc_rule_id = (SELECT qc_rule_id FROM qc_rule WHERE name = 'Ct_range_primary') THEN 'Ct'
  WHEN qr.qc_rule_id = (SELECT qc_rule_id FROM qc_rule WHERE name = 'Replicate_sd_limit') THEN 'Replicate_SD'
  ELSE qr.metric_name
END
WHERE qr.metric_name IS NULL OR qr.metric_name NOT IN ('Ct','Replicate_SD');


-- Run-level QC results: NTC, positive control, and run failure rate
WITH
  runs AS (
    SELECT run_id, run_name
    FROM assay_run
    WHERE run_name IN ('RUN-N1-001', 'RUN-N1-002')
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
    WHEN q.name = 'Ct_ntc_contamination'  AND r.run_name = 'RUN-N1-001' THEN 37.5
    WHEN q.name = 'Ct_ntc_contamination'  AND r.run_name = 'RUN-N1-002' THEN 28.0  -- contaminated NTC
    WHEN q.name = 'Run_positive_control'  AND r.run_name = 'RUN-N1-001' THEN 20.1
    WHEN q.name = 'Run_positive_control'  AND r.run_name = 'RUN-N1-002' THEN 21.0
    WHEN q.name = 'Run_failure_rate_high' THEN fs.fail_rate
    ELSE 0.0
  END AS metric_value,
  CASE
    WHEN q.name = 'Ct_ntc_contamination'  AND r.run_name = 'RUN-N1-002' THEN 'FAIL'
    WHEN q.name = 'Run_failure_rate_high' AND fs.fail_rate > 0.10 THEN 'FAIL'
    WHEN q.name = 'Run_positive_control'  THEN 'PASS'
    ELSE 'PASS'
  END AS result_flag
FROM runs r
JOIN rules q ON TRUE
LEFT JOIN failure_stats fs ON fs.run_id = r.run_id
ON CONFLICT DO NOTHING;

