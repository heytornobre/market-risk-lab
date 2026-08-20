"""Historical VaR and equal-probability fractional-boundary CVaR."""

import math
from typing import Literal

import numpy as np

from market_risk_engine.exceptions import DataValidationError

QUANTILE_METHOD: Literal["linear"] = "linear"
MINIMUM_OBSERVATIONS = 30


def overlapping_compounded_returns(daily_returns: np.ndarray, horizon: int) -> np.ndarray:
    returns = np.asarray(daily_returns, dtype=np.float64)
    if horizon <= 0:
        raise DataValidationError("horizon must be positive")
    if not np.isfinite(returns).all() or np.any(returns <= -1):
        raise DataValidationError("daily simple returns must be finite and greater than -100%")
    if len(returns) < horizon:
        raise DataValidationError("insufficient observations for the requested horizon")
    windows = np.lib.stride_tricks.sliding_window_view(returns, horizon)
    return np.prod(1.0 + windows, axis=1) - 1.0


def empirical_var_cvar(
    returns: np.ndarray,
    confidence_level: float,
    *,
    minimum_observations: int = MINIMUM_OBSERVATIONS,
) -> tuple[float, float]:
    values = np.asarray(returns, dtype=np.float64)
    if not 0 < confidence_level < 1:
        raise DataValidationError("confidence level must be strictly between 0 and 1")
    if len(values) < minimum_observations:
        raise DataValidationError(
            f"at least {minimum_observations} return observations are required"
        )
    if not np.isfinite(values).all() or np.any(values <= -1):
        raise DataValidationError("returns must be finite and greater than -100%")
    losses = -values
    raw_var = float(np.quantile(losses, confidence_level, method=QUANTILE_METHOD))

    descending = np.sort(losses)[::-1]
    tail_mass = len(descending) * (1.0 - confidence_level)
    full_count = int(math.floor(tail_mass))
    fractional_mass = tail_mass - full_count
    tail_sum = float(descending[:full_count].sum())
    if fractional_mass > 0:
        tail_sum += fractional_mass * float(descending[full_count])
    raw_cvar = tail_sum / tail_mass
    return max(raw_var, 0.0), max(raw_cvar, 0.0)
