from __future__ import annotations

from typing import Literal

import streamlit as st

from app.pages import qc_investigation, run_overview, trend_analysis


def main() -> None:
    st.set_page_config(
        page_title="Assay Run & QC Dashboard",
        layout="wide",
    )

    st.sidebar.title("Assay Run & QC Dashboard")

    # Show a notice when running without a real database
    from app.db import is_demo_mode
    if is_demo_mode():
        st.info(
            "🔬 **Demo 模式** — 正在展示合成 qPCR 数据（40 runs / 90 天），"
            "内含仪器降级、Ct 漂移和 NTC 污染三个可分析场景。"
            "接入 PostgreSQL 数据库并删除 `DEMO_MODE` 环境变量可切换至真实数据。",
            icon=None,
        )

    page: Literal["Run overview", "QC investigation", "Trend analysis"] = st.sidebar.radio(
        "页面",
        options=["Run overview", "QC investigation", "Trend analysis"],
        format_func=lambda x: {
            "Run overview": "📋 Run 概览",
            "QC investigation": "🔍 QC 调查",
            "Trend analysis": "📈 趋势分析",
        }[x],
        index=0,
    )

    if page == "Run overview":
        run_overview.render()
    elif page == "QC investigation":
        qc_investigation.render()
    else:
        trend_analysis.render()


if __name__ == "__main__":
    main()
