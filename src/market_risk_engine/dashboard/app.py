"""Local Streamlit entry point for the synthetic read-only dashboard."""

from __future__ import annotations

import argparse
from pathlib import Path

import streamlit as st

from market_risk_engine.dashboard.data import (
    list_succeeded_runs,
    load_dashboard_data,
    validate_existing_database,
)
from market_risk_engine.dashboard.views import render_dashboard
from market_risk_engine.exceptions import MarketRiskLabError


def _database_argument() -> Path:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--database", type=Path, default=Path("var/market-risk-lab.db"))
    arguments, _ = parser.parse_known_args()
    return Path(arguments.database)


def main() -> None:
    st.set_page_config(
        page_title="Market Risk Lab",
        page_icon="📊",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    try:
        database = validate_existing_database(_database_argument())
        runs = list_succeeded_runs(database)
        if not runs:
            load_dashboard_data(database)
            return
        labels = {run.label: run.run_id for run in runs}
        with st.sidebar:
            st.header("Run controls")
            selected_label = st.selectbox("Succeeded calculation run", tuple(labels))
            if st.button("Refresh from database", help="Rerun read-only database queries"):
                st.rerun()
            st.caption("Succeeded runs only · explicit read-only refresh")
        render_dashboard(load_dashboard_data(database, labels[selected_label]))
    except MarketRiskLabError as error:
        st.error(str(error))
        st.info("Prepare the local synthetic database with the documented CLI workflow.")


if __name__ == "__main__":
    main()
