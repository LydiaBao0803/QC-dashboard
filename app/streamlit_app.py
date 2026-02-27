from __future__ import annotations

from typing import Literal

import streamlit as st

from app.pages import qc_investigation, run_overview


def main() -> None:
    st.set_page_config(
        page_title="Assay Run & QC Dashboard",
        layout="wide",
    )

    st.sidebar.title("Assay Run & QC Dashboard")

    page: Literal["Run overview", "QC investigation"] = st.sidebar.radio(
        "View",
        options=["Run overview", "QC investigation"],
        index=0,
    )

    if page == "Run overview":
        run_overview.render()
    else:
        qc_investigation.render()


if __name__ == "__main__":
    main()

