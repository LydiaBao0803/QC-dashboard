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
            "**Demo Mode** — Displaying synthetic qPCR data (40 runs / 90 days) "
            "with three embedded analytical stories: instrument degradation, Ct drift, "
            "and NTC contamination. Connect a PostgreSQL database to switch to live data.",
            icon=None,
        )

    page: Literal["Run overview", "QC investigation", "Trend analysis"] = st.sidebar.radio(
        "Navigation",
        options=["Run overview", "QC investigation", "Trend analysis"],
        format_func=lambda x: {
            "Run overview": "Run Overview",
            "QC investigation": "QC Investigation",
            "Trend analysis": "Trend Analysis",
        }[x],
        key="sidebar_page",
    )

    if page == "Run overview":
        run_overview.render()
    elif page == "QC investigation":
        qc_investigation.render()
    else:
        trend_analysis.render()


if __name__ == "__main__":
    main()
