"""Normal variance-covariance VaR with sample mean and sample covariance."""

import math

import numpy as np
from scipy.stats import norm  # type: ignore[import-untyped]

from market_risk_engine.exceptions import DataValidationError
from market_risk_engine.risk.historical import MINIMUM_OBSERVATIONS


def normal_var(
    instrument_returns: np.ndarray,
    weights: np.ndarray,
    confidence_level: float,
    horizon: int,
    *,
    include_mean: bool = True,
) -> float:
    returns = np.asarray(instrument_returns, dtype=np.float64)
    portfolio_weights = np.asarray(weights, dtype=np.float64)
    if returns.ndim != 2 or returns.shape[1] != len(portfolio_weights):
        raise DataValidationError("instrument return matrix and weights are incompatible")
    if len(returns) < MINIMUM_OBSERVATIONS:
        raise DataValidationError(
            f"at least {MINIMUM_OBSERVATIONS} return observations are required"
        )
    if not np.isfinite(returns).all() or not np.isfinite(portfolio_weights).all():
        raise DataValidationError("returns and weights must be finite")
    if not 0 < confidence_level < 1 or horizon <= 0:
        raise DataValidationError("invalid confidence level or horizon")
    daily_mean = float(returns.mean(axis=0) @ portfolio_weights) if include_mean else 0.0
    covariance = np.atleast_2d(np.cov(returns, rowvar=False, ddof=1))
    variance = float(portfolio_weights @ covariance @ portfolio_weights)
    if variance < -1e-14:
        raise DataValidationError("portfolio variance is materially negative")
    sigma_horizon = math.sqrt(max(variance, 0.0) * horizon)
    mean_horizon = daily_mean * horizon
    raw_var = -mean_horizon + float(norm.ppf(confidence_level)) * sigma_horizon
    return max(raw_var, 0.0)
