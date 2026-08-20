"""EUR valuation, effective-position selection, and constant-weight return construction."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from market_risk_engine.domain.models import Instrument, Position
from market_risk_engine.exceptions import DataValidationError
from market_risk_engine.storage.sqlite import SQLiteRepository


@dataclass(frozen=True)
class PortfolioData:
    portfolio_id: str
    as_of_date: date
    effective_position_date: date
    held_instrument_ids: tuple[str, ...]
    benchmark_instrument_id: str
    quantities: np.ndarray
    market_values_eur: np.ndarray
    gross_market_value_eur: float
    weights: np.ndarray
    base_prices: pd.DataFrame
    instrument_simple_returns: pd.DataFrame
    instrument_log_returns: pd.DataFrame
    portfolio_simple_returns: pd.Series
    benchmark_simple_returns: pd.Series
    instruments: dict[str, Instrument]


def _select_positions(
    positions: list[Position], portfolio_id: str, as_of_date: date
) -> tuple[date, list[Position]]:
    candidates = [
        item
        for item in positions
        if item.portfolio_id == portfolio_id and item.effective_date <= as_of_date
    ]
    if not candidates:
        raise DataValidationError(
            f"no effective positions for portfolio {portfolio_id!r} on or before {as_of_date}"
        )
    effective_date = max(item.effective_date for item in candidates)
    snapshot = sorted(
        [item for item in candidates if item.effective_date == effective_date],
        key=lambda item: item.instrument_id,
    )
    if any(item.quantity < 0 for item in snapshot):
        raise DataValidationError("A4 rejects short positions; use a long-only position snapshot")
    return effective_date, snapshot


def prepare_portfolio_data(
    repository: SQLiteRepository,
    *,
    portfolio_id: str,
    as_of_date: date,
    benchmark_instrument_id: str,
) -> PortfolioData:
    instruments = {item.instrument_id: item for item in repository.instruments()}
    if benchmark_instrument_id not in instruments:
        raise DataValidationError(f"unknown benchmark instrument: {benchmark_instrument_id}")
    effective_date, positions = _select_positions(repository.positions(), portfolio_id, as_of_date)
    held_ids = tuple(item.instrument_id for item in positions)
    required_ids = set(held_ids) | {benchmark_instrument_id}
    unknown = required_ids - set(instruments)
    if unknown:
        raise DataValidationError(f"unknown instruments in calculation request: {sorted(unknown)}")

    prices = [
        item
        for item in repository.prices()
        if item.instrument_id in required_ids and item.date <= as_of_date
    ]
    if not prices:
        raise DataValidationError(f"no prices are available on or before {as_of_date}")
    price_dates: dict[str, set[date]] = {instrument_id: set() for instrument_id in required_ids}
    for item in prices:
        price_dates[item.instrument_id].add(item.date)
    reference_dates = price_dates[sorted(required_ids)[0]]
    for instrument_id in sorted(required_ids):
        if price_dates[instrument_id] != reference_dates:
            raise DataValidationError(
                f"incomplete price coverage for {instrument_id} through {as_of_date}"
            )
        if as_of_date not in price_dates[instrument_id]:
            raise DataValidationError(f"missing as-of price for {instrument_id} on {as_of_date}")

    fx_rates = {
        (item.date, item.base_currency.value, item.quote_currency.value): item.rate
        for item in repository.fx_rates()
        if item.date <= as_of_date
    }
    rows: list[tuple[date, str, float]] = []
    for price in prices:
        instrument = instruments[price.instrument_id]
        fx_key = (price.date, "EUR", instrument.quote_currency.value)
        if fx_key not in fx_rates:
            raise DataValidationError(
                f"missing EUR/{instrument.quote_currency.value} FX rate on {price.date}"
            )
        base_value = price.close * instrument.price_multiplier * fx_rates[fx_key]
        if not np.isfinite(base_value) or base_value <= 0:
            raise DataValidationError(
                f"invalid converted price for {price.instrument_id} on {price.date}"
            )
        rows.append((price.date, price.instrument_id, base_value))

    frame = pd.DataFrame(rows, columns=["date", "instrument_id", "base_close"])
    base_prices = frame.pivot(index="date", columns="instrument_id", values="base_close")
    base_prices = base_prices.sort_index().sort_index(axis=1)
    if base_prices.isna().any().any():
        raise DataValidationError("converted price matrix contains missing values")

    quantities = np.array([item.quantity for item in positions], dtype=np.float64)
    held_price_frame = base_prices.loc[:, list(held_ids)]
    as_of_prices = held_price_frame.reindex(index=[as_of_date]).to_numpy(dtype=np.float64)[0]
    market_values = quantities * as_of_prices
    gross_value = float(np.abs(market_values).sum())
    if not np.isfinite(gross_value) or gross_value <= 0:
        raise DataValidationError("portfolio gross market value must be positive and finite")
    weights = market_values / gross_value

    simple_returns = base_prices.pct_change(fill_method=None).iloc[1:]
    values = simple_returns.to_numpy(dtype=np.float64)
    if len(simple_returns) == 0 or not np.isfinite(values).all():
        raise DataValidationError("converted price history produces invalid simple returns")
    held_returns = simple_returns.loc[:, list(held_ids)]
    portfolio_returns = pd.Series(
        held_returns.to_numpy(dtype=np.float64) @ weights,
        index=held_returns.index,
        name="portfolio_return",
    )
    benchmark_returns = simple_returns.loc[:, benchmark_instrument_id].rename("benchmark_return")
    held_price_values = held_price_frame.to_numpy(dtype=np.float64)
    log_price_frame = pd.DataFrame(
        np.log(held_price_values),
        index=held_price_frame.index,
        columns=held_price_frame.columns,
    )
    log_returns = log_price_frame.diff().iloc[1:]
    if not np.isfinite(log_returns.to_numpy(dtype=np.float64)).all():
        raise DataValidationError("converted price history produces invalid log returns")

    return PortfolioData(
        portfolio_id=portfolio_id,
        as_of_date=as_of_date,
        effective_position_date=effective_date,
        held_instrument_ids=held_ids,
        benchmark_instrument_id=benchmark_instrument_id,
        quantities=quantities,
        market_values_eur=market_values,
        gross_market_value_eur=gross_value,
        weights=weights,
        base_prices=base_prices,
        instrument_simple_returns=held_returns,
        instrument_log_returns=log_returns,
        portfolio_simple_returns=portfolio_returns,
        benchmark_simple_returns=benchmark_returns,
        instruments=instruments,
    )
