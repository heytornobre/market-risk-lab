import math

import numpy as np
import pytest

from market_risk_engine.exceptions import DataValidationError
from market_risk_engine.risk.factors import factor_metrics


def test_factor_metrics_match_known_linear_series() -> None:
    benchmark = np.linspace(-0.02, 0.02, 40)
    portfolio = 1.5 * benchmark + 0.0002
    metrics = factor_metrics(portfolio, benchmark, annual_risk_free_rate=None)
    assert metrics["beta"] == pytest.approx(1.5)
    assert metrics["correlation"] == pytest.approx(1.0)
    assert metrics["annualised_volatility"] == pytest.approx(
        np.std(portfolio, ddof=1) * math.sqrt(252)
    )
    assert metrics["tracking_error"] == pytest.approx(
        np.std(portfolio - benchmark, ddof=1) * math.sqrt(252)
    )
    assert "annualised_excess_return_alpha" not in metrics


def test_alpha_requires_and_uses_explicit_risk_free_rate() -> None:
    benchmark = np.linspace(-0.02, 0.02, 40)
    portfolio = 1.5 * benchmark + 0.0002
    annual_rate = 0.03
    daily_rate = (1 + annual_rate) ** (1 / 252) - 1
    metrics = factor_metrics(portfolio, benchmark, annual_risk_free_rate=annual_rate)
    expected = (0.0002 + 0.5 * daily_rate) * 252
    assert metrics["annualised_excess_return_alpha"] == pytest.approx(expected)


def test_zero_variance_benchmark_is_rejected() -> None:
    with pytest.raises(DataValidationError, match="benchmark variance"):
        factor_metrics(np.linspace(0, 0.01, 40), np.ones(40), annual_risk_free_rate=None)
