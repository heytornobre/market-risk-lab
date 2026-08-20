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
    residual_variance = 1.0 - np.square(loadings).sum(axis=1)
    if np.any(residual_variance <= 0):
        raise ValueError("factor loadings leave no positive idiosyncratic variance")
    correlation = loadings @ loadings.T + np.diag(residual_variance)
    correlation = (correlation + correlation.T) / 2.0
    if np.linalg.eigvalsh(correlation).min() < -1e-12:
        raise ValueError("constructed correlation matrix is not positive semidefinite")
    return labels, correlation


def _correlated_shocks(spec: FixtureSpec, observations: int) -> tuple[list[str], np.ndarray]:
    labels, correlation = build_correlation_matrix(spec)
    eigenvalues, eigenvectors = np.linalg.eigh(correlation)
    root = eigenvectors @ np.diag(np.sqrt(np.clip(eigenvalues, 0.0, None)))
    random = np.random.default_rng(spec.seed).standard_normal((observations, len(labels)))
    return labels, random @ root.T


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
    increments = daily_drift + daily_volatility * shocks[1:]
    values[1:] = initial_value * np.exp(np.cumsum(increments))
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
