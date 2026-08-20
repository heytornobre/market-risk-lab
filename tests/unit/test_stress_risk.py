from datetime import date

import numpy as np
import pandas as pd
import pytest

from market_risk_engine.domain.models import AssetClass, Currency, Instrument
from market_risk_engine.exceptions import DataValidationError
from market_risk_engine.risk.portfolio import PortfolioData
from market_risk_engine.risk.stress import StressScenario, apply_scenario


def _portfolio_data() -> PortfolioData:
    empty = pd.DataFrame()
    series = pd.Series(dtype=float)
    instruments = {
        "LAB_A": Instrument(
            instrument_id="LAB_A",
            display_name="Synthetic A",
            asset_class=AssetClass.EQUITY,
            quote_currency=Currency.USD,
            price_multiplier=1.0,
        ),
        "LAB_B": Instrument(
            instrument_id="LAB_B",
            display_name="Synthetic B",
            asset_class=AssetClass.GOVERNMENT_BOND,
            quote_currency=Currency.EUR,
            price_multiplier=1.0,
        ),
    }
    return PortfolioData(
        portfolio_id="LAB",
        as_of_date=date(2025, 1, 1),
        effective_position_date=date(2025, 1, 1),
        held_instrument_ids=("LAB_A", "LAB_B"),
        benchmark_instrument_id="LAB_A",
        quantities=np.array([1.0, 1.0]),
        market_values_eur=np.array([100.0, 200.0]),
        gross_market_value_eur=300.0,
        weights=np.array([1 / 3, 2 / 3]),
        base_prices=empty,
        instrument_simple_returns=empty,
        instrument_log_returns=empty,
        portfolio_simple_returns=series,
        benchmark_simple_returns=series,
        instruments=instruments,
    )


def test_local_and_fx_shocks_combine_multiplicatively_with_sign() -> None:
    scenario = StressScenario(
        scenario_id="known",
        version="1",
        description="Known vector",
        asset_class_shocks={AssetClass.EQUITY: -0.10},
        currency_shocks={Currency.USD: -0.20},
    )
    result = apply_scenario(_portfolio_data(), scenario)
    assert result.pnl_eur == pytest.approx(100 * ((1 - 0.10) * (1 - 0.20) - 1))
    assert result.portfolio_return == pytest.approx(-28 / 300)
    assert result.coverage_ratio == pytest.approx(1 / 3)
    assert result.uncovered_instruments == ("LAB_B",)


def test_stress_gain_direction_is_preserved() -> None:
    scenario = StressScenario(
        scenario_id="gain",
        version="1",
        description="Positive shock",
        instrument_shocks={"LAB_B": 0.10},
    )
    result = apply_scenario(_portfolio_data(), scenario)
    assert result.pnl_eur == pytest.approx(20.0)
    assert result.portfolio_return > 0


def test_conflicting_shock_mapping_is_rejected() -> None:
    scenario = StressScenario(
        scenario_id="conflict",
        version="1",
        description="Ambiguous local shock",
        asset_class_shocks={AssetClass.EQUITY: -0.1},
        instrument_shocks={"LAB_A": -0.2},
    )
    with pytest.raises(DataValidationError, match="conflicting"):
        apply_scenario(_portfolio_data(), scenario)


def test_zero_coverage_is_unavailable_not_zero_loss() -> None:
    scenario = StressScenario(
        scenario_id="none",
        version="1",
        description="No covered holdings",
        currency_shocks={Currency.GBP: -0.1},
    )
    with pytest.raises(DataValidationError, match="zero covered"):
        apply_scenario(_portfolio_data(), scenario)
