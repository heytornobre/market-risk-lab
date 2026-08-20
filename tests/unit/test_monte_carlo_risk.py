import numpy as np
import pytest

from market_risk_engine.exceptions import DataValidationError
from market_risk_engine.risk.monte_carlo import (
    monte_carlo_var_cvar,
    validate_or_repair_covariance,
)


def test_singular_covariance_is_supported() -> None:
    matrix = np.array([[0.04, 0.04], [0.04, 0.04]])
    result = validate_or_repair_covariance(matrix)
    assert result.repaired is False
    assert np.linalg.eigvalsh(result.covariance).min() >= -1e-14


def test_tiny_negative_eigenvalue_is_repaired() -> None:
    matrix = np.diag([1.0, -1e-12])
    result = validate_or_repair_covariance(matrix)
    assert result.repaired is True
    assert np.linalg.eigvalsh(result.covariance).min() >= 0


def test_materially_indefinite_covariance_fails() -> None:
    with pytest.raises(DataValidationError, match="materially indefinite"):
        validate_or_repair_covariance(np.diag([1.0, -1e-4]))


def test_degenerate_log_return_model_has_analytical_zero_loss() -> None:
    history = np.full((40, 2), 0.001)
    var, cvar, _ = monte_carlo_var_cvar(
        history,
        np.array([0.4, 0.6]),
        0.99,
        10,
        seed=7,
        simulations=1_000,
    )
    assert var == 0.0
    assert cvar == 0.0


def test_pcg64_regression_vector_is_deterministic() -> None:
    first = np.column_stack(
        (
            np.linspace(-0.015, 0.018, 60),
            np.sin(np.arange(60)) * 0.01,
        )
    )
    result = monte_carlo_var_cvar(
        first,
        np.array([0.6, 0.4]),
        0.95,
        3,
        seed=12345,
        simulations=2_000,
    )
    repeated = monte_carlo_var_cvar(
        first,
        np.array([0.6, 0.4]),
        0.95,
        3,
        seed=12345,
        simulations=2_000,
    )
    assert result[0] == repeated[0]
    assert result[1] == repeated[1]
    assert result[0] == pytest.approx(0.01618663365820886, abs=1e-15)
    assert result[1] == pytest.approx(0.020827740270347495, abs=1e-15)
