import math

import numpy as np
import pytest

from market_risk_engine.risk.parametric import normal_var

Z_95 = 1.6448536269514722


def test_one_asset_parametric_var_matches_analytical_identity() -> None:
    pattern = np.array([-0.02, -0.01, 0.0, 0.01, 0.02])
    returns = np.tile(pattern, 6).reshape(-1, 1)
    sample_mean = float(returns.mean())
    sample_std = float(returns.std(ddof=1))
    expected = -10 * sample_mean + Z_95 * math.sqrt(10) * sample_std
    assert normal_var(returns, np.array([1.0]), 0.95, 10) == pytest.approx(expected)


def test_parametric_mean_convention_changes_result() -> None:
    returns = np.tile(np.array([-0.01, 0.0, 0.0103]), 10).reshape(-1, 1)
    with_mean = normal_var(returns, np.array([1.0]), 0.95, 1, include_mean=True)
    without_mean = normal_var(returns, np.array([1.0]), 0.95, 1, include_mean=False)
    assert without_mean - with_mean == pytest.approx(float(returns.mean()))
