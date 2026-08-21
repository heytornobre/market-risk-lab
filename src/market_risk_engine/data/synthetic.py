"""Deterministic correlated synthetic market and FX path generation."""

from __future__ import annotations

import csv
import hashlib
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from market_risk_engine.domain.models import Currency, FixtureSpec

CSV_FLOAT_FORMAT = ".10f"
GENERATOR_VERSION = "fixed-order-cholesky-v1"
CORRELATION_TOLERANCE = 1e-12


def build_correlation_matrix(spec: FixtureSpec) -> tuple[list[str], np.ndarray]:
    """Build a PSD correlation matrix from named factor loadings plus idiosyncratic risk."""
    factors = sorted(spec.factors)
    instruments = sorted(spec.instruments, key=lambda item: item.instrument_id)
    fx_series = sorted(spec.fx_series, key=lambda item: item.quote_currency.value)
    labels = [item.instrument_id for item in instruments]
    labels.extend(f"FX_{item.quote_currency.value}" for item in fx_series)
    series_loadings = [item.factor_loadings for item in instruments]
    series_loadings.extend(item.factor_loadings for item in fx_series)
    loadings = np.array(
        [[values.get(factor, 0.0) for factor in factors] for values in series_loadings],
        dtype=np.float64,
    )
    residual_variance = np.array(
        [1.0 - math.fsum(float(value) ** 2 for value in row) for row in loadings],
        dtype=np.float64,
    )
    if np.any(residual_variance <= 0):
        raise ValueError("factor loadings leave no positive idiosyncratic variance")
    correlation = np.empty((len(loadings), len(loadings)), dtype=np.float64)
    for row in range(len(loadings)):
        for column in range(len(loadings)):
            correlation[row, column] = math.fsum(
                float(left) * float(right)
                for left, right in zip(loadings[row], loadings[column], strict=True)
            )
            if row == column:
                correlation[row, column] += residual_variance[row]
    if not np.array_equal(correlation, correlation.T):
        raise ValueError("constructed correlation matrix is not symmetric")
    return labels, correlation


def build_cholesky_factor(correlation: np.ndarray) -> np.ndarray:
    """Return a fixed-order lower Cholesky factor after validating reconstruction."""
    if correlation.ndim != 2 or correlation.shape[0] != correlation.shape[1]:
        raise ValueError("correlation matrix must be square")
    if not np.array_equal(correlation, correlation.T):
        raise ValueError("correlation matrix must be symmetric")
    factor = np.zeros_like(correlation, dtype=np.float64)
    for row in range(len(correlation)):
        for column in range(row + 1):
            subtotal = math.fsum(
                float(factor[row, index]) * float(factor[column, index]) for index in range(column)
            )
            remainder = float(correlation[row, column]) - subtotal
            if row == column:
                if remainder <= 0.0:
                    raise ValueError("correlation matrix must be positive definite")
                factor[row, column] = math.sqrt(remainder)
            else:
                factor[row, column] = remainder / float(factor[column, column])
    for row in range(len(correlation)):
        for column in range(len(correlation)):
            reconstructed = math.fsum(
                float(factor[row, index]) * float(factor[column, index])
                for index in range(min(row, column) + 1)
            )
            if not math.isclose(
                reconstructed,
                float(correlation[row, column]),
                rel_tol=CORRELATION_TOLERANCE,
                abs_tol=CORRELATION_TOLERANCE,
            ):
                raise ValueError("Cholesky factor does not reconstruct the correlation matrix")
    return factor


def _correlated_shocks(spec: FixtureSpec, observations: int) -> tuple[list[str], np.ndarray]:
    if spec.generator_version != GENERATOR_VERSION:
        raise ValueError(
            f"unsupported fixture generator version: {spec.generator_version}; "
            f"expected {GENERATOR_VERSION}"
        )
    labels, correlation = build_correlation_matrix(spec)
    root = build_cholesky_factor(correlation)
    random = np.random.default_rng(spec.seed).standard_normal((observations, len(labels)))
    shocks = np.empty_like(random)
    for observation in range(observations):
        for series in range(len(labels)):
            shocks[observation, series] = math.fsum(
                float(random[observation, factor]) * float(root[series, factor])
                for factor in range(series + 1)
            )
    return labels, shocks


def _geometric_path(
    initial_value: float,
    annual_return: float,
    annual_volatility: float,
    shocks: np.ndarray,
    trading_days: int,
) -> np.ndarray:
    values = np.empty(len(shocks), dtype=np.float64)
    values[0] = initial_value
    daily_drift = (annual_return - 0.5 * annual_volatility**2) / trading_days
    daily_volatility = annual_volatility / math.sqrt(trading_days)
    current = initial_value
    for index, shock in enumerate(shocks[1:], start=1):
        current *= math.exp(daily_drift + daily_volatility * float(shock))
        values[index] = current
    return values


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def generate_fixtures(spec: FixtureSpec, output_directory: Path) -> dict[str, str]:
    """Generate stable CSV fixtures and return their SHA-256 hashes."""
    dates = [item.date() for item in pd.bdate_range(spec.start_date, spec.end_date)]
    labels, shocks = _correlated_shocks(spec, len(dates))
    shock_by_label = {label: shocks[:, index] for index, label in enumerate(labels)}

    instruments = sorted(spec.instruments, key=lambda item: item.instrument_id)
    instrument_rows = [
        {
            "instrument_id": item.instrument_id,
            "display_name": item.display_name,
            "asset_class": item.asset_class.value,
            "quote_currency": item.quote_currency.value,
            "price_multiplier": format(item.price_multiplier, CSV_FLOAT_FORMAT),
            "factor_classification": item.factor_classification or "",
        }
        for item in instruments
    ]
    price_rows: list[dict[str, str]] = []
    for item in instruments:
        values = _geometric_path(
            item.initial_price,
            item.annual_return,
            item.annual_volatility,
            shock_by_label[item.instrument_id],
            spec.trading_days_per_year,
        )
        price_rows.extend(
            {
                "date": day.isoformat(),
                "instrument_id": item.instrument_id,
                "close": format(value, CSV_FLOAT_FORMAT),
            }
            for day, value in zip(dates, values, strict=True)
        )

    position_rows = [
        {
            "portfolio_id": item.portfolio_id,
            "effective_date": item.effective_date.isoformat(),
            "instrument_id": item.instrument_id,
            "quantity": format(item.quantity, CSV_FLOAT_FORMAT),
            "unit_cost": "" if item.unit_cost is None else format(item.unit_cost, CSV_FLOAT_FORMAT),
        }
        for item in sorted(
            spec.positions,
            key=lambda position: (
                position.portfolio_id,
                position.effective_date,
                position.instrument_id,
            ),
        )
    ]

    fx_rows: list[dict[str, str]] = [
        {
            "date": day.isoformat(),
            "base_currency": Currency.EUR.value,
            "quote_currency": Currency.EUR.value,
            "rate": format(1.0, CSV_FLOAT_FORMAT),
        }
        for day in dates
    ]
    for fx_item in sorted(spec.fx_series, key=lambda series: series.quote_currency.value):
        values = _geometric_path(
            fx_item.initial_rate,
            fx_item.annual_return,
            fx_item.annual_volatility,
            shock_by_label[f"FX_{fx_item.quote_currency.value}"],
            spec.trading_days_per_year,
        )
        fx_rows.extend(
            {
                "date": day.isoformat(),
                "base_currency": spec.base_currency.value,
                "quote_currency": fx_item.quote_currency.value,
                "rate": format(value, CSV_FLOAT_FORMAT),
            }
            for day, value in zip(dates, values, strict=True)
        )
    fx_rows.sort(key=lambda row: (row["date"], row["base_currency"], row["quote_currency"]))

    files = {
        "instruments.csv": (
            [
                "instrument_id",
                "display_name",
                "asset_class",
                "quote_currency",
                "price_multiplier",
                "factor_classification",
            ],
            instrument_rows,
        ),
        "positions.csv": (
            ["portfolio_id", "effective_date", "instrument_id", "quantity", "unit_cost"],
            position_rows,
        ),
        "prices.csv": (["date", "instrument_id", "close"], price_rows),
        "fx_rates.csv": (["date", "base_currency", "quote_currency", "rate"], fx_rows),
    }
    hashes: dict[str, str] = {}
    for filename, (fieldnames, rows) in files.items():
        path = output_directory / filename
        _write_csv(path, fieldnames, rows)
        hashes[filename] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes
