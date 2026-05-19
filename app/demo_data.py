"""Synthetic demo data for Streamlit Cloud deployment (no PostgreSQL required).

When DEMO_MODE=true is set as an environment variable (or Streamlit secret),
the app uses the DataFrames defined here instead of hitting the real database.

Embedded analytical stories
----------------------------
1. Thermo-001 instrument degradation  — fail rate rises from ~5 % in weeks 1-3
   to ~22 % in weeks 7-9, then drops back to ~6 % after maintenance (week 10+).
2. Ct-value drift                     — mean Ct climbs steadily over runs 1-25
   (~+0.06 per run, total +1.4 Ct units), then resets when a new reagent lot
   is introduced at run 26.
3. NTC contamination event            — runs 18-21 show a spike in NTC_check
   failures (~35 % of samples), then clears up.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

# ── demo mode helper ──────────────────────────────────────────────────────────


def is_demo_mode() -> bool:
    """Return True when demo mode should be used.

    Three ways demo mode activates (in order):
    1. DEMO_MODE env-var is set to a truthy value ("1", "true", "yes").
    2. DEMO_MODE Streamlit secret is set to a truthy value.
    3. Auto-detect: no DB_HOST is configured in either env-var or Streamlit
       secrets — meaning there is no real database to connect to.
    """
    _truthy = ("1", "true", "yes")

    if os.environ.get("DEMO_MODE", "").strip().lower() in _truthy:
        return True
    try:
        import streamlit as st
        if str(st.secrets.get("DEMO_MODE", "")).strip().lower() in _truthy:
            return True
    except Exception:
        pass

    has_db_host_env = bool(os.environ.get("DB_HOST"))
    has_db_host_secret = False
    try:
        import streamlit as st
        has_db_host_secret = bool(st.secrets.get("DB_HOST"))
    except Exception:
        pass

    if not has_db_host_env and not has_db_host_secret:
        return True

    return False


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
_SAMPLES = [f"SMP-{i:04d}" for i in range(1, 61)]   # 60 unique samples
_PATIENTS = [f"PAT-{i:04d}" for i in range(1, 61)]
_METRICS = ["Ct_value", "efficiency", "NTC_check", "pos_ctrl_Ct"]

_RNG = np.random.default_rng(42)

# ── generate synthetic runs (40 runs over ~90 days) ───────────────────────────

_BASE_DT = datetime(2026, 5, 19, 17, 0, 0)
_N_RUNS = 40
_SAMPLES_PER_RUN = 10

_RUNS_RAW: list[dict] = []

for _i in range(_N_RUNS):
    _run_id = _i + 1
    # Space runs ~2.3 days apart so span ≈ 90 days
    _days_ago = int((_N_RUNS - 1 - _i) * 2.3)
    _hour_jitter = int(_RNG.integers(7, 18))
    _started = _BASE_DT - timedelta(days=_days_ago, hours=_hour_jitter)
    _completed = _started + timedelta(hours=2 + int(_RNG.integers(0, 3)))
    _assay_id = (_i % 5) + 1
    _assay_name = ASSAYS.loc[_assay_id - 1, "name"]
    _instrument = _INSTRUMENTS[_i % 3]          # Thermo / Bio-Rad / Applied cycle
    _operator = _OPERATORS[(_i + 1) % 3]        # offset so operator ≠ instrument
    _status = "FAILED" if _run_id in (20, 38) else "COMPLETED"

    _RUNS_RAW.append({
        "run_id": _run_id,
        "run_name": f"RUN-2026-{_run_id:03d}",
        "started_at": _started,
        "completed_at": _completed,
        "status": _status,
        "assay_id": _assay_id,
        "assay_name": _assay_name,
        "instrument_name": _instrument,
        "operator": _operator,
    })

RUNS_DF = pd.DataFrame(_RUNS_RAW)


# ── QC-generation helpers ─────────────────────────────────────────────────────

def _ct_drift_offset(run_id: int) -> float:
    """Story 2 – Ct drift: +0.06/run for runs 1-25, then resets (new reagent lot)."""
    if run_id <= 25:
        return (run_id - 1) * 0.06          # 0.0 → +1.44
    # New reagent lot at run 26: Ct drops back toward baseline over ~5 runs
    reset_progress = min((run_id - 25) / 5.0, 1.0)
    return 1.44 * (1.0 - reset_progress)


def _efficiency_base(instrument: str, run_id: int) -> float:
    """Story 1 side-effect – Bio-Rad-002 efficiency slowly declines over time."""
    if instrument == "Bio-Rad-002":
        return max(89.0, 97.0 - run_id * 0.18)   # 97 % → ~90 % over 40 runs
    elif instrument == "Applied-003":
        return 95.5 + float(_RNG.uniform(-0.8, 0.8))
    else:  # Thermo-001
        return 93.5 + float(_RNG.uniform(-1.2, 1.2))


def _base_fail_prob(instrument: str, run_id: int) -> float:
    """Story 1 – Thermo-001 degrades between runs 19-33, recovers after maintenance."""
    base = 0.05
    if instrument == "Thermo-001":
        if 19 <= run_id <= 33:
            boost = min((run_id - 18) * 0.018, 0.20)
            return base + boost                 # peaks at ~25 %
        if run_id > 33:
            return 0.04                         # post-maintenance: slightly better
    if instrument == "Applied-003":
        return base * 0.55                      # best performer
    return base                                 # Bio-Rad-002: stable baseline


def _ntc_contaminated(run_id: int) -> bool:
    """Story 3 – NTC contamination event in runs 18-21 (~35 % per sample)."""
    if run_id in (18, 19, 20, 21):
        return float(_RNG.random()) < 0.35
    return False


# ── generate synthetic QC results ─────────────────────────────────────────────

_qc_rows: list[dict] = []
_rs_id = 1

for _run in _RUNS_RAW:
    _rid = _run["run_id"]
    _inst = _run["instrument_name"]
    _run_failed = _run["status"] == "FAILED"

    for _si in range(_SAMPLES_PER_RUN):
        _smp_idx = (_rid * 3 + _si) % len(_SAMPLES)
        _ext_id = _SAMPLES[_smp_idx]
        _pat = _PATIENTS[_smp_idx]
        _col = (_si % 12) + 1
        _row_letter = chr(ord("A") + (_si // 12))
        _well = f"{_row_letter}{_col:02d}"

        # Base Ct for this sample position + story-2 drift
        _base_ct = 27.5 + (_si % 6) * 0.4
        _expected_ct = _base_ct + _ct_drift_offset(_rid)

        # Deliberate sample-level failures
        _hard_fail = (_run_failed and _si == 0)
        _prob_fail = _base_fail_prob(_inst, _rid)
        _soft_fail = (not _hard_fail) and (float(_RNG.random()) < _prob_fail) and (_si % 4 == 0)
        _fail_flag = _hard_fail or _soft_fail

        for _rep in range(1, 3):   # 2 replicates
            for _metric in _METRICS:
                if _metric == "Ct_value":
                    _noise_sd = 0.30 if _inst == "Thermo-001" else 0.15
                    _noise = float(_RNG.normal(0, _noise_sd))
                    _val = _expected_ct + _noise + (0.20 if _rep == 2 else 0.0)
                    _dev = abs(_val - _base_ct)
                    _flag = "PASS" if _dev < 1.0 else ("WARN" if _dev < 2.0 else "FAIL")

                elif _metric == "efficiency":
                    _eff_base = _efficiency_base(_inst, _rid)
                    _val = _eff_base + float(_RNG.normal(0, 1.5))
                    _flag = "PASS" if _val >= 90 else ("WARN" if _val >= 85 else "FAIL")

                elif _metric == "NTC_check":
                    if _ntc_contaminated(_rid):
                        _val = float(_RNG.uniform(24.0, 32.0))
                        _flag = "FAIL"
                    else:
                        _val = 0.0
                        _flag = "PASS"

                elif _metric == "pos_ctrl_Ct":
                    _ctrl_base = 21.5 + 0.4 * (_rid % 5)
                    _val = _ctrl_base + float(_RNG.normal(0, 0.3))
                    _flag = "PASS" if 18 <= _val <= 26 else ("WARN" if 17 <= _val <= 27 else "FAIL")

                else:
                    _val, _flag = 0.0, "PASS"

                # Hard-fail override
                if _fail_flag:
                    _flag = "FAIL"

                _qc_rows.append({
                    "run_sample_id": _rs_id,
                    "run_id": _rid,
                    "external_id": _ext_id,
                    "patient_id": _pat,
                    "well_position": _well,
                    "replicate_number": _rep,
                    "expected_ct": round(float(_expected_ct), 3),
                    "assay_status": "FAIL" if _fail_flag else "PASS",
                    "metric_name": _metric,
                    "metric_value": round(float(_val), 3),
                    "result_flag": _flag,
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

        ct_vals = qc_run[qc_run["metric_name"] == "Ct_value"]["metric_value"]
        avg_ct = float(ct_vals.mean()) if not ct_vals.empty else float("nan")

        records.append({
            "run_id": rid,
            "run_name": run_row["run_name"],
            "started_at": run_row["started_at"],
            "completed_at": run_row["completed_at"],
            "status": run_row["status"],
            "assay_name": run_row["assay_name"],
            "instrument_name": run_row["instrument_name"],
            "operator": run_row["operator"],
            "sample_count": sample_count,
            "pass_count": pass_count,
            "warn_count": warn_count,
            "fail_count": fail_count,
            "fail_rate": fail_rate,
            "avg_ct_value": round(avg_ct, 3) if not pd.isna(avg_ct) else float("nan"),
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


def get_metric_breakdown_demo(start_date=None, end_date=None) -> pd.DataFrame:
    """Aggregate pass / warn / fail counts per QC metric across a date range."""
    df = RUNS_DF.copy()
    if start_date:
        start_dt = datetime.combine(start_date, datetime.min.time())
        df = df[df["started_at"] >= start_dt]
    if end_date:
        end_dt = datetime.combine(end_date + timedelta(days=1), datetime.min.time())
        df = df[df["started_at"] < end_dt]

    run_ids = df["run_id"].tolist()
    qc = QC_DF[QC_DF["run_id"].isin(run_ids)]

    records = []
    for metric in _METRICS:
        sub = qc[qc["metric_name"] == metric]
        total = len(sub)
        records.append({
            "metric_name": metric,
            "total_count": total,
            "pass_count": int((sub["result_flag"] == "PASS").sum()),
            "warn_count": int((sub["result_flag"] == "WARN").sum()),
            "fail_count": int((sub["result_flag"] == "FAIL").sum()),
        })
    return pd.DataFrame(records)
