"""Sample-statistic factor metrics against an explicit EUR-converted benchmark."""

import math

import numpy as np

from market_risk_engine.exceptions import DataValidationError

TRADING_DAYS = 252


def factor_metrics(
    portfolio_returns: np.ndarray,
    benchmark_returns: np.ndarray,
    *,
    annual_risk_free_rate: float | None,
) -> dict[str, float]:
    portfolio = np.asarray(portfolio_returns, dtype=np.float64)
    benchmark = np.asarray(benchmark_returns, dtype=np.float64)
    if portfolio.ndim != 1 or benchmark.ndim != 1 or len(portfolio) != len(benchmark):
        raise DataValidationError("portfolio and benchmark returns must be aligned vectors")
    if len(portfolio) < 30:
        raise DataValidationError(
            "at least 30 aligned observations are required for factor metrics"
        )
    if not np.isfinite(portfolio).all() or not np.isfinite(benchmark).all():
        raise DataValidationError("factor return series must be finite")
    benchmark_variance = float(np.var(benchmark, ddof=1))
    if benchmark_variance <= 0:
        raise DataValidationError("benchmark variance must be positive")
    covariance = float(np.cov(portfolio, benchmark, ddof=1)[0, 1])
    beta = covariance / benchmark_variance
    correlation = float(np.corrcoef(portfolio, benchmark)[0, 1])
    metrics = {
        "beta": beta,
        "correlation": correlation,
        "annualised_volatility": float(np.std(portfolio, ddof=1) * math.sqrt(TRADING_DAYS)),
        "tracking_error": float(np.std(portfolio - benchmark, ddof=1) * math.sqrt(TRADING_DAYS)),
    }
    if annual_risk_free_rate is not None:
        if not math.isfinite(annual_risk_free_rate) or annual_risk_free_rate <= -1:
            raise DataValidationError("annual risk-free rate must be finite and greater than -1")
        daily_risk_free = (1.0 + annual_risk_free_rate) ** (1.0 / TRADING_DAYS) - 1.0
        daily_alpha = float(
            np.mean((portfolio - daily_risk_free) - beta * (benchmark - daily_risk_free))
        )
        metrics["annualised_excess_return_alpha"] = daily_alpha * TRADING_DAYS
    return metrics
