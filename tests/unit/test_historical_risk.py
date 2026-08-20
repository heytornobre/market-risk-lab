import numpy as np
import pytest

from market_risk_engine.exceptions import DataValidationError
from market_risk_engine.risk.historical import (
    empirical_var_cvar,
    overlapping_compounded_returns,
)


def test_known_loss_sample_linear_quantile_and_tail_mass() -> None:
    returns = np.array([-0.10, -0.05, 0.0, 0.02])
    var, cvar = empirical_var_cvar(returns, 0.75, minimum_observations=1)
    assert var == pytest.approx(0.0625)
    assert cvar == pytest.approx(0.10)


def test_positive_returns_are_not_converted_to_losses_with_abs() -> None:
    returns = np.array([0.01, 0.02, 0.03, 0.04])
    var, cvar = empirical_var_cvar(returns, 0.75, minimum_observations=1)
    assert var == 0.0
    assert cvar == 0.0


def test_tied_boundary_observations_have_rank_mass_not_boolean_tail() -> None:
    losses = np.array([0.20, 0.10, 0.10, 0.10, 0.0, 0.0, -0.01, -0.02])
    var, cvar = empirical_var_cvar(-losses, 0.625, minimum_observations=1)
    assert var == pytest.approx(0.10)
    assert cvar == pytest.approx((0.20 + 0.10 + 0.10) / 3)


def test_fractional_tail_boundary() -> None:
    losses = np.array([0.20, 0.10, 0.05, 0.0, -0.01])
    _, cvar = empirical_var_cvar(-losses, 0.70, minimum_observations=1)
    assert cvar == pytest.approx((0.20 + 0.5 * 0.10) / 1.5)


def test_overlapping_multiday_compounding() -> None:
    returns = np.array([0.10, -0.10, 0.20])
    compounded = overlapping_compounded_returns(returns, 2)
    assert compounded == pytest.approx(np.array([-0.01, 0.08]))


def test_historical_risk_rejects_insufficient_observations() -> None:
    with pytest.raises(DataValidationError, match="at least 30"):
        empirical_var_cvar(np.array([-0.01, 0.01]), 0.95)
