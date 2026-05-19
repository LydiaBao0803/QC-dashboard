from pathlib import Path
from typing import Optional

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from app.db import fetch_df
from app.queries import get_run_qc_details, get_sample_qc_history


# ── helpers ───────────────────────────────────────────────────────────────────


def _load_run_metadata(run_id: int) -> pd.DataFrame:
    from app.db import is_demo_mode
    if is_demo_mode():
        from app.demo_data import get_run_metadata_demo
        return get_run_metadata_demo(run_id)

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


def _metric_summary_chart(qc_df: pd.DataFrame) -> go.Figure:
    """Grouped bar chart: pass / warn / fail count per QC metric."""
    summary = []
    for metric in qc_df["metric_name"].unique():
        sub = qc_df[qc_df["metric_name"] == metric]
        summary.append({
            "metric": metric,
            "PASS": int((sub["result_flag"] == "PASS").sum()),
            "WARN": int((sub["result_flag"] == "WARN").sum()),
            "FAIL": int((sub["result_flag"] == "FAIL").sum()),
        })
    df = pd.DataFrame(summary)

    fig = go.Figure()
    for flag, color in [("PASS", "#2ecc71"), ("WARN", "#f39c12"), ("FAIL", "#e74c3c")]:
        fig.add_trace(go.Bar(
            name=flag,
            x=df["metric"],
            y=df[flag],
            marker_color=color,
            text=df[flag],
            textposition="outside",
        ))
    fig.update_layout(
        barmode="group",
        height=300,
        margin=dict(l=0, r=0, t=10, b=0),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        xaxis_title="QC 指标",
        yaxis=dict(showgrid=True, gridcolor="#e0e0e0", title="次数"),
    )
    return fig


def _well_heatmap(qc_df: pd.DataFrame) -> go.Figure:
    """96-well style heatmap: each well coloured by worst flag across metrics."""
    FLAG_ORDER = {"FAIL": 2, "WARN": 1, "PASS": 0}
    FLAG_COLOR = {0: "#2ecc71", 1: "#f39c12", 2: "#e74c3c", -1: "#cccccc"}

    # Aggregate per well: worst flag wins
    well_df = (
        qc_df
        .assign(flag_score=qc_df["result_flag"].map(FLAG_ORDER).fillna(-1).astype(int))
        .groupby("well_position")["flag_score"]
        .max()
        .reset_index()
    )

    # Parse rows (A-H) and columns (01-12)
    well_df["row"] = well_df["well_position"].str[0]
    well_df["col"] = well_df["well_position"].str[1:].astype(int)

    rows = sorted(well_df["row"].unique())
    cols = sorted(well_df["col"].unique())

    # Build z-matrix and text-matrix
    row_idx = {r: i for i, r in enumerate(rows)}
    col_idx = {c: i for i, c in enumerate(cols)}
    import numpy as np
    z = np.full((len(rows), len(cols)), -1, dtype=int)
    hover = [["" for _ in cols] for _ in rows]

    for _, wr in well_df.iterrows():
        ri = row_idx[wr["row"]]
        ci = col_idx[int(wr["col"])]
        z[ri][ci] = int(wr["flag_score"])
        flag_name = {2: "FAIL", 1: "WARN", 0: "PASS", -1: "N/A"}[int(wr["flag_score"])]
        hover[ri][ci] = f"{wr['well_position']}: {flag_name}"

    # Custom discrete colorscale
    colorscale = [
        [0.0, "#cccccc"],   # -1 → grey
        [0.25, "#cccccc"],
        [0.25, "#2ecc71"],  # 0 → green
        [0.5, "#2ecc71"],
        [0.5, "#f39c12"],   # 1 → orange
        [0.75, "#f39c12"],
        [0.75, "#e74c3c"],  # 2 → red
        [1.0, "#e74c3c"],
    ]

    fig = go.Figure(go.Heatmap(
        z=z,
        x=[str(c) for c in cols],
        y=rows,
        text=hover,
        hovertemplate="%{text}<extra></extra>",
        colorscale=colorscale,
        zmin=-1,
        zmax=2,
        showscale=False,
        xgap=3,
        ygap=3,
    ))
    fig.update_layout(
        height=max(200, len(rows) * 36 + 60),
        margin=dict(l=30, r=10, t=10, b=40),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(title="列", side="bottom", tickfont=dict(size=10)),
        yaxis=dict(title="行", autorange="reversed", tickfont=dict(size=10)),
    )
    # Legend annotation
    fig.add_annotation(
        text="🟢 PASS  🟠 WARN  🔴 FAIL  ⬜ N/A",
        xref="paper", yref="paper",
        x=0, y=-0.18, showarrow=False,
        font=dict(size=11),
    )
    return fig


# ── by-run view ───────────────────────────────────────────────────────────────


def _render_by_run() -> None:
    st.subheader("按 Run 调查")

    default_run_id = st.session_state.get("selected_run_id", 1)
    run_id = st.number_input("Run ID", min_value=1, step=1, value=int(default_run_id))

    if st.button("加载 Run"):
        st.session_state["selected_run_id"] = int(run_id)

    run_id = int(st.session_state.get("selected_run_id", run_id))

    meta_df = _load_run_metadata(run_id)
    if meta_df.empty:
        st.info(f"未找到 run_id = {run_id} 的数据。")
        return

    # ── metadata ──────────────────────────────────────────────────────────────
    st.markdown("**Run 基本信息**")
    st.table(meta_df)

    qc_df = get_run_qc_details(run_id)
    if qc_df.empty:
        st.info("该 run 暂无 QC 结果。")
        return

    # ── KPI strip ─────────────────────────────────────────────────────────────
    total_checks = len(qc_df)
    pass_n = int((qc_df["result_flag"] == "PASS").sum())
    warn_n = int((qc_df["result_flag"] == "WARN").sum())
    fail_n = int((qc_df["result_flag"] == "FAIL").sum())
    fail_pct = fail_n / total_checks * 100 if total_checks else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("✅ PASS", pass_n)
    c2.metric("🟡 WARN", warn_n)
    c3.metric("❌ FAIL", fail_n)
    c4.metric("📊 失败率", f"{fail_pct:.1f}%")

    st.divider()

    # ── well heatmap ──────────────────────────────────────────────────────────
    st.subheader("孔板 QC 热力图")
    st.caption("每个孔位显示该孔最差的 QC 结果（FAIL > WARN > PASS）")
    if "well_position" in qc_df.columns:
        st.plotly_chart(_well_heatmap(qc_df), use_container_width=True)

    # ── metric distribution chart ─────────────────────────────────────────────
    st.subheader("各指标 Pass / Warn / Fail 分布")
    st.plotly_chart(_metric_summary_chart(qc_df), use_container_width=True)

    # ── raw data table ────────────────────────────────────────────────────────
    with st.expander("查看原始 QC 明细数据"):
        st.dataframe(qc_df, use_container_width=True, hide_index=True)

    with st.expander("高级：SQL 查询参考"):
        st.markdown("**Run drill-down SQL（简化版）**")
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
            st.text(plan_path.read_text())
        else:
            st.info("EXPLAIN plan file `explain/run_drilldown.txt` not generated yet.")


# ── by-sample view ────────────────────────────────────────────────────────────


def _ct_history_chart(history_df: pd.DataFrame) -> Optional[go.Figure]:
    """Line chart of Ct value over time for a given sample."""
    ct_df = history_df[history_df["metric_name"] == "Ct_value"].copy()
    if ct_df.empty:
        return None
    ct_df["started_at"] = pd.to_datetime(ct_df["started_at"])
    ct_df = ct_df.sort_values("started_at")

    fig = px.line(
        ct_df,
        x="started_at",
        y="metric_value",
        color="assay_name",
        markers=True,
        symbol="result_flag",
        symbol_map={"PASS": "circle", "WARN": "diamond", "FAIL": "x"},
        labels={"started_at": "Run 日期", "metric_value": "Ct 值", "assay_name": "Assay"},
        color_discrete_sequence=px.colors.qualitative.Set2,
    )
    fig.update_traces(marker_size=8, line_width=1.8)
    # Reference band 25-30
    fig.add_hrect(y0=25, y1=30, fillcolor="rgba(46,204,113,0.08)",
                  line_width=0, annotation_text="正常范围", annotation_position="top left")
    fig.update_layout(
        height=300,
        margin=dict(l=0, r=0, t=20, b=0),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        xaxis=dict(showgrid=True, gridcolor="#e0e0e0"),
        yaxis=dict(showgrid=True, gridcolor="#e0e0e0"),
    )
    return fig


def _render_by_sample() -> None:
    st.subheader("按样本调查")

    default_ext: Optional[str] = st.session_state.get("selected_sample_external_id")
    external_id = st.text_input(
        "样本 External ID",
        value=default_ext or "",
        placeholder="例如：SMP-0001",
    )

    if not external_id:
        st.info("输入样本 ID 以查看其历史 QC 记录。")
        return

    if st.button("加载样本历史"):
        st.session_state["selected_sample_external_id"] = external_id

    external_id = st.session_state.get("selected_sample_external_id", external_id)

    history_df = get_sample_qc_history(external_id)
    if history_df.empty:
        st.info(f"未找到样本 `{external_id}` 的 QC 历史记录。")
        return

    # ── Ct history chart ──────────────────────────────────────────────────────
    st.subheader(f"样本 `{external_id}` 的 Ct 值历史趋势")
    ct_fig = _ct_history_chart(history_df)
    if ct_fig:
        st.plotly_chart(ct_fig, use_container_width=True)
    else:
        st.info("该样本无 Ct_value 记录。")

    # ── flag summary ──────────────────────────────────────────────────────────
    flag_counts = history_df["result_flag"].value_counts()
    f1, f2, f3 = st.columns(3)
    f1.metric("✅ PASS", int(flag_counts.get("PASS", 0)))
    f2.metric("🟡 WARN", int(flag_counts.get("WARN", 0)))
    f3.metric("❌ FAIL", int(flag_counts.get("FAIL", 0)))

    # ── full history table ────────────────────────────────────────────────────
    with st.expander("查看完整历史记录"):
        st.dataframe(history_df, use_container_width=True, hide_index=True)

    with st.expander("高级：SQL 查询参考"):
        st.code(
            """
SELECT
  s.external_id,
  ar.run_name,
  ar.started_at,
  rs.well_position,
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
            """,
            language="sql",
        )
        plan_path = Path("explain/sample_history.txt")
        if plan_path.exists():
            st.text(plan_path.read_text())
        else:
            st.info("EXPLAIN plan file `explain/sample_history.txt` not generated yet.")


# ── page renderer ─────────────────────────────────────────────────────────────


def render() -> None:
    st.header("QC 调查")

    mode = st.radio(
        "模式",
        options=["By run", "By sample"],
        format_func=lambda x: "按 Run 调查" if x == "By run" else "按样本追踪",
        index=0 if st.session_state.get("qc_mode", "By run") == "By run" else 1,
    )
    st.session_state["qc_mode"] = mode

    if mode == "By run":
        _render_by_run()
    else:
        _render_by_sample()
