"""Synthetic demo data for Streamlit Cloud deployment (no PostgreSQL required).

When DEMO_MODE=true is set as an environment variable (or Streamlit secret),
the app uses the DataFrames defined here instead of hitting the real database.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

import pandas as pd

# ── demo mode helper ──────────────────────────────────────────────────────────


def is_demo_mode() -> bool:
    """Return True when the DEMO_MODE env-var / Streamlit secret is set."""
    return os.environ.get("DEMO_MODE", "").strip().lower() in ("1", "true", "yes")


# ── static reference data ─────────────────────────────────────────────────────

ASSAYS = pd.DataFrame([
    {"assay_id": 1, "name": "COV-PCR"},
    {"assay_id": 2, "name": "RSV-PCR"},
    {"assay_id": 3, "name": "FLU-A"},
    {"assay_id": 4, "name": "FLU-B"},
    {"assay_id": 5, "name": "MPOX"},
])

_INSTRUMENTS = ["Thermo-001", "Bio-Rad-002", "Applied-003"]
_OPERATORS = ["Alice Chen", "Bob Patel", "Cynthia Lam"]
_SAMPLES = [f"SMP-{i:04d}" for i in range(1, 25)]
_PATIENTS = [f"PAT-{i:04d}" for i in range(1, 25)]
_METRICS = ["Ct_value", "efficiency", "NTC_check", "pos_ctrl_Ct"]

# ── generate synthetic runs ───────────────────────────────────────────────────

_BASE_DT = datetime(2026, 5, 17, 17, 0, 0)
_RUNS_RAW: list[dict] = []

for _i in range(10):
    _run_id = _i + 1
    _days_ago = (9 - _i) * 3          # spread runs over ~27 days
    _started = _BASE_DT - timedelta(days=_days_ago, hours=_i % 6)
    _completed = _started + timedelta(hours=3 + (_i % 2))
    _assay_id = (_i % 5) + 1
    _assay_name = ASSAYS[ASSAYS["assay_id"] == _assay_id]["name"].values[0]

    _RUNS_RAW.append({
        "run_id": _run_id,
        "run_name": f"RUN-2026-{_run_id:03d}",
        "started_at": _started,
        "completed_at": _completed,
        "status": "COMPLETED" if _i < 8 else "FAILED",
        "assay_id": _assay_id,
        "assay_name": _assay_name,
        "instrument_name": _INSTRUMENTS[_i % 3],
        "operator": _OPERATORS[_i % 3],
    })

RUNS_DF = pd.DataFrame(_RUNS_RAW)

# ── generate synthetic QC results ─────────────────────────────────────────────

_qc_rows: list[dict] = []
_rs_id = 1

for _run in _RUNS_RAW:
    _rid = _run["run_id"]

    for _si in range(8):           # 8 samples per run
        _smp_idx = (_rid + _si) % len(_SAMPLES)
        _ext_id = _SAMPLES[_smp_idx]
        _pat = _PATIENTS[_smp_idx]
        _col = (_si % 12) + 1
        _row_letter = chr(ord("A") + (_si // 12))
        _well = f"{_row_letter}{_col:02d}"
        _expected_ct = 28.0 + (_si % 5) * 0.5
        # Introduce deliberate failures for two scenarios
        _fail_flag = (_run["status"] == "FAILED" and _si == 0) or (_rid % 3 == 0 and _si == 2)

        for _rep in range(1, 3):   # 2 replicates
            for _metric in _METRICS:
                if _metric == "Ct_value":
                    _val = _expected_ct + (0.3 if _rep == 2 else 0.0)
                    _flag = "PASS" if abs(_val - _expected_ct) < 1.0 else "FAIL"
                elif _metric == "efficiency":
                    _val = 95.0 + (2.0 * (_si % 3) - 2.0)
                    _flag = "PASS" if _val >= 90 else "WARN"
                elif _metric == "NTC_check":
                    _val = 0.0 if not _fail_flag else 28.5
                    _flag = "PASS" if _val == 0.0 else "FAIL"
                elif _metric == "pos_ctrl_Ct":
                    _val = 22.0 + 0.5 * (_rid % 4)
                    _flag = "PASS" if 18 <= _val <= 26 else "WARN"
                else:
                    _val, _flag = 0.0, "PASS"

                _qc_rows.append({
                    "run_sample_id": _rs_id,
                    "run_id": _rid,
                    "external_id": _ext_id,
                    "patient_id": _pat,
                    "well_position": _well,
                    "replicate_number": _rep,
                    "expected_ct": _expected_ct,
                    "assay_status": "FAIL" if _fail_flag else "PASS",
                    "metric_name": _metric,
                    "metric_value": round(_val, 3),
                    "result_flag": "FAIL" if _fail_flag else _flag,
                })
            _rs_id += 1

QC_DF = pd.DataFrame(_qc_rows)

# ── public getters ────────────────────────────────────────────────────────────


def get_assays_demo() -> pd.DataFrame:
    return ASSAYS.copy()


def get_run_metadata_demo(run_id: int) -> pd.DataFrame:
    cols = ["run_id", "run_name", "started_at", "completed_at",
            "status", "assay_name", "instrument_name", "operator"]
    return RUNS_DF[RUNS_DF["run_id"] == run_id][cols].copy().reset_index(drop=True)


def get_run_qc_summary_demo(
    start_date=None,
    end_date=None,
    assay_id: int | None = None,
    statuses=None,
    limit: int = 200,
) -> pd.DataFrame:
    df = RUNS_DF.copy()

    if start_date:
        start_dt = datetime.combine(start_date, datetime.min.time())
        df = df[df["started_at"] >= start_dt]
    if end_date:
        end_dt = datetime.combine(end_date + timedelta(days=1), datetime.min.time())
        df = df[df["started_at"] < end_dt]
    if assay_id is not None:
        df = df[df["assay_id"] == assay_id]
    if statuses:
        df = df[df["status"].isin(list(statuses))]

    records = []
    for _, run_row in df.iterrows():
        rid = int(run_row["run_id"])
        qc_run = QC_DF[QC_DF["run_id"] == rid]
        sample_count = int(qc_run["run_sample_id"].nunique())
        pass_count = int((qc_run["result_flag"] == "PASS").sum())
        warn_count = int((qc_run["result_flag"] == "WARN").sum())
        fail_count = int((qc_run["result_flag"] == "FAIL").sum())
        total_qc = pass_count + warn_count + fail_count
        fail_rate = fail_count / total_qc if total_qc > 0 else float("nan")
        records.append({
            "run_id": rid,
            "run_name": run_row["run_name"],
            "started_at": run_row["started_at"],
            "completed_at": run_row["completed_at"],
            "status": run_row["status"],
            "assay_name": run_row["assay_name"],
            "instrument_name": run_row["instrument_name"],
            "sample_count": sample_count,
            "pass_count": pass_count,
            "warn_count": warn_count,
            "fail_count": fail_count,
            "fail_rate": fail_rate,
        })

    out = pd.DataFrame(records) if records else pd.DataFrame()
    if not out.empty:
        out = out.head(limit)
    return out


def get_run_qc_details_demo(run_id: int) -> pd.DataFrame:
    cols = [
        "run_sample_id", "external_id", "patient_id", "well_position",
        "replicate_number", "expected_ct", "assay_status",
        "metric_name", "metric_value", "result_flag",
    ]
    return QC_DF[QC_DF["run_id"] == run_id][cols].copy().reset_index(drop=True)


def get_sample_qc_history_demo(external_id: str) -> pd.DataFrame:
    qc = QC_DF[QC_DF["external_id"] == external_id].copy()
    if qc.empty:
        return pd.DataFrame()
    run_cols = ["run_id", "run_name", "started_at", "status", "assay_name", "instrument_name"]
    merged = qc.merge(RUNS_DF[run_cols], on="run_id", how="left")
    out_cols = [
        "external_id", "run_id", "run_name", "started_at", "status",
        "assay_name", "instrument_name", "well_position", "replicate_number",
        "expected_ct", "metric_name", "metric_value", "result_flag",
    ]
    return merged[out_cols].sort_values("started_at", ascending=False).reset_index(drop=True)
