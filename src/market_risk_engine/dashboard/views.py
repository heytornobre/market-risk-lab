"""Streamlit rendering kept separate from dashboard data access."""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from market_risk_engine.dashboard.data import DashboardData, risk_comparison_rows


def _eur(value: float) -> str:
    return f"€{value:,.0f}"


def _signed_eur(value: float) -> str:
    if value < 0:
        return f"−€{abs(value):,.0f}"
    if value > 0:
        return f"+€{value:,.0f}"
    return "€0"


def render_dashboard(data: DashboardData) -> None:
    st.title("Market Risk Lab")
    st.markdown(
        "A reproducible Python and SQLite pipeline comparing portfolio risk across "
        "historical, parametric, and Monte Carlo models."
    )
    st.caption("Deterministic synthetic data only · engineering demonstration")

    overview = st.columns(4)
    overview[0].markdown(f"**Portfolio**  \n`{data.run['portfolio_id']}`")
    overview[1].metric("As of", str(data.run["input_data_cutoff"]))
    overview[2].metric("Market value", _eur(data.portfolio_market_value_eur))
    overview[3].metric("Base currency", str(data.run["base_currency"]))
    detail = st.columns(3)
    detail[0].metric("Instruments", data.instrument_count)
    detail[1].metric("Fixture version", str(data.run["fixture_version"]))
    detail[2].metric("Calculation version", str(data.run["package_version"]))
    st.caption(
        f"Succeeded run `{data.run['run_id']}` · data cutoff {data.run['input_data_cutoff']}"
    )

    st.subheader("Risk comparison")
    comparison = pd.DataFrame(risk_comparison_rows(data))
    confidences = sorted({float(row["confidence_level"]) for row in data.risk_rows})
    horizons = sorted({int(row["horizon"]) for row in data.risk_rows})
    selectors = st.columns(2)
    confidence = selectors[0].selectbox(
        "Confidence level", confidences, format_func=lambda value: f"{value:.0%}"
    )
    horizon = selectors[1].selectbox(
        "Horizon",
        horizons,
        format_func=lambda value: f"{value} business day{'s' if value != 1 else ''}",
    )
    selected_label = f"{confidence:.0%} · {horizon}d"
    available = comparison[
        (comparison["Label"] == selected_label) & comparison["Loss %"].notna()
    ].copy()
    model_order = ["Historical", "Parametric", "Monte Carlo"]
    available["Model"] = pd.Categorical(available["Model"], categories=model_order, ordered=True)
    st.caption(f"{confidence:.0%} confidence · {horizon}-day horizon")
    st.vega_lite_chart(
        available,
        {
            "mark": {"type": "bar"},
            "encoding": {
                "x": {"field": "Loss %", "type": "quantitative", "title": "Loss (%)"},
                "y": {
                    "field": "Model",
                    "type": "nominal",
                    "sort": model_order,
                    "title": "Model",
                    "axis": {"labelAngle": 0},
                },
                "yOffset": {"field": "Measure"},
                "color": {
                    "field": "Measure",
                    "type": "nominal",
                    "scale": {
                        "domain": ["VaR", "CVaR"],
                        "range": ["#D97706", "#176B87"],
                    },
                },
            },
        },
        width="stretch",
    )
    if confidence == 0.95 and horizon == 1:
        minimum = float(available["Loss %"].min())
        maximum = float(available["Loss %"].max())
        st.caption(
            "Synthetic result: the 95% one-day loss estimates span "
            f"{minimum:.2f}% to {maximum:.2f}% across the available model measures."
        )
    display = comparison.copy()
    display["Loss %"] = display["Loss %"].map(
        lambda value: "Unavailable" if pd.isna(value) else f"{value:.2f}%"
    )
    display["Loss EUR"] = display["Loss EUR"].map(
        lambda value: "Unavailable" if pd.isna(value) else _eur(float(value))
    )
    with st.expander("Accessible results table · all models, confidence levels, and horizons"):
        st.dataframe(display, hide_index=True, width="stretch")
    st.caption("Parametric CVaR is not calculated and is shown as unavailable—not zero.")

    st.subheader("Stress scenarios")
    stress_records: list[dict[str, Any]] = []
    for row in data.stress_rows:
        pnl = float(row["pnl_eur"])
        stress_records.append(
            {
                "Scenario": str(row["scenario_id"]).replace("_", " ").title(),
                "Direction": "Gain" if pnl > 0 else "Loss" if pnl < 0 else "Flat",
                "Return": f"{float(row['portfolio_return']):+.2%}",
                "P&L": _signed_eur(pnl),
                "Coverage": f"{float(row['coverage_ratio']):.1%}",
                "Covered value": _eur(float(row["covered_market_value_eur"])),
                "Gross value": _eur(float(row["gross_market_value_eur"])),
                "Uncovered": ", ".join(row["uncovered_instruments"]) or "None",
            }
        )
    st.dataframe(pd.DataFrame(stress_records), hide_index=True, width="stretch")

    st.subheader("Factor metrics")
    metric_names = {
        "beta": "Beta",
        "correlation": "Correlation",
        "annualised_volatility": "Annualised volatility",
        "tracking_error": "Tracking error",
        "annualised_excess_return_alpha": "Annualised alpha",
    }
    factor_columns = st.columns(min(5, len(data.factor_rows)))
    for column, row in zip(factor_columns, data.factor_rows, strict=True):
        metric = str(row["metric"])
        value = float(row["value"])
        formatted = (
            f"{value:.2%}"
            if metric
            in {
                "annualised_volatility",
                "tracking_error",
                "annualised_excess_return_alpha",
            }
            else f"{value:.3f}"
        )
        column.metric(metric_names[metric], formatted)
    benchmark = data.model_identity["benchmark"]
    if data.model_identity["annual_risk_free_rate"] is None:
        st.caption(f"Benchmark: `{benchmark}` · Alpha unavailable: no risk-free rate was supplied.")
    else:
        st.caption(f"Benchmark: `{benchmark}`")

    with st.expander("Provenance and limitations"):
        simulations = data.model_identity.get("monte_carlo_simulations", "unknown")
        simulation_label = f"{simulations:,}" if isinstance(simulations, int) else str(simulations)
        monte_carlo = (
            f"PCG64 seed `{data.model_identity['monte_carlo_seed']}`, "
            f"{simulation_label} simulations"
        )
        parameter_hash = str(data.model_identity["parameter_hash"])
        short_hash = f"{parameter_hash[:12]}…{parameter_hash[-8:]}"
        st.markdown(
            f"""
- **Effective position date:** {data.run["effective_position_date"]}
- **Data cutoff:** {data.run["input_data_cutoff"]}
- **Portfolio convention:** fixed as-of weights with daily simple returns
- **FX treatment:** same-date conversion to EUR before return aggregation
- **Monte Carlo:** {monte_carlo}
- **Model identity:** `{short_hash}`
- **Historical tail:** linear quantile; equal-probability fractional-boundary CVaR
- **Stress scenario version:** {data.model_identity["stress_scenario_version"]}
- **Benchmark:** `{benchmark}`

**Documentation:** `docs/methodology.md` — formulas, conventions, and validation details.

**Complete persisted parameter hash:** `{parameter_hash}`

**Limitations:** Synthetic data only. Engineering demonstration. Not a realised-performance
backtest. Not production risk infrastructure. Not regulatory reporting. Not investment advice.
"""
        )
    if data.failed_runs:
        with st.expander("Operational history: failed runs"):
            st.dataframe(pd.DataFrame(data.failed_runs), hide_index=True, width="stretch")
