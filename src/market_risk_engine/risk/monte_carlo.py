"""Deterministic multivariate log-return Monte Carlo VaR and CVaR."""

from dataclasses import dataclass

import numpy as np

from market_risk_engine.exceptions import DataValidationError
from market_risk_engine.risk.historical import MINIMUM_OBSERVATIONS, empirical_var_cvar

COVARIANCE_TOLERANCE = 1e-10


@dataclass(frozen=True)
class CovarianceRepair:
    covariance: np.ndarray
    repaired: bool
    minimum_eigenvalue: float
    tolerance: float


def validate_or_repair_covariance(
    covariance: np.ndarray, *, tolerance: float = COVARIANCE_TOLERANCE
) -> CovarianceRepair:
    matrix = np.asarray(covariance, dtype=np.float64)
    if matrix.ndim != 2 or matrix.shape[0] != matrix.shape[1]:
        raise DataValidationError("covariance matrix must be square")
    if not np.isfinite(matrix).all():
        raise DataValidationError("covariance matrix must be finite")
    symmetric = (matrix + matrix.T) / 2.0
    eigenvalues, eigenvectors = np.linalg.eigh(symmetric)
    scale = max(1.0, float(np.abs(eigenvalues).max()))
    absolute_tolerance = tolerance * scale
    minimum = float(eigenvalues.min())
    if minimum < -absolute_tolerance:
        raise DataValidationError(
            f"covariance matrix is materially indefinite (minimum eigenvalue {minimum:.6g})"
        )
    repaired = minimum < 0
    if repaired:
        symmetric = eigenvectors @ np.diag(np.clip(eigenvalues, 0.0, None)) @ eigenvectors.T
        symmetric = (symmetric + symmetric.T) / 2.0
    return CovarianceRepair(symmetric, repaired, minimum, absolute_tolerance)


def monte_carlo_var_cvar(
    log_returns: np.ndarray,
    weights: np.ndarray,
    confidence_level: float,
    horizon: int,
    *,
    seed: int,
    simulations: int,
) -> tuple[float, float, CovarianceRepair]:
    history = np.asarray(log_returns, dtype=np.float64)
    portfolio_weights = np.asarray(weights, dtype=np.float64)
    if history.ndim != 2 or history.shape[1] != len(portfolio_weights):
        raise DataValidationError("log-return matrix and weights are incompatible")
    if len(history) < MINIMUM_OBSERVATIONS:
        raise DataValidationError(
            f"at least {MINIMUM_OBSERVATIONS} log-return observations are required"
        )
    if not np.isfinite(history).all() or not np.isfinite(portfolio_weights).all():
        raise DataValidationError("log returns and weights must be finite")
    if horizon <= 0 or simulations < 1_000 or seed < 0:
        raise DataValidationError("invalid Monte Carlo horizon, simulation count, or seed")
    mean = history.mean(axis=0)
    repair = validate_or_repair_covariance(np.cov(history, rowvar=False, ddof=1))
    eigenvalues, eigenvectors = np.linalg.eigh(repair.covariance)
    root = eigenvectors @ np.diag(np.sqrt(np.clip(eigenvalues, 0.0, None)))
    generator = np.random.Generator(np.random.PCG64(seed))
    independent = generator.standard_normal((simulations, horizon, history.shape[1]))
    simulated_daily = independent @ root.T + mean
    horizon_log_returns = simulated_daily.sum(axis=1)
    terminal_simple_returns = np.expm1(horizon_log_returns)
    if np.any(terminal_simple_returns <= -1) or not np.isfinite(terminal_simple_returns).all():
        raise DataValidationError("Monte Carlo generated invalid terminal simple returns")
    portfolio_returns = terminal_simple_returns @ portfolio_weights
    var, cvar = empirical_var_cvar(portfolio_returns, confidence_level, minimum_observations=1_000)
    return var, cvar, repair
