"""Strict TOML and CSV loading with full cross-file coverage validation."""

from __future__ import annotations

import csv
import hashlib
import json
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import BaseModel, ValidationError

from market_risk_engine.domain.models import (
    Currency,
    FixtureSpec,
    FxRate,
    Instrument,
    Position,
    Price,
)
from market_risk_engine.exceptions import DataValidationError


@dataclass(frozen=True)
class FixtureBundle:
    spec: FixtureSpec
    instruments: tuple[Instrument, ...]
    positions: tuple[Position, ...]
    prices: tuple[Price, ...]
    fx_rates: tuple[FxRate, ...]
    specification_hash: str


def load_specification(path: Path) -> FixtureSpec:
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
        return FixtureSpec.model_validate(payload)
    except (OSError, tomllib.TOMLDecodeError, ValidationError) as error:
        raise DataValidationError(f"invalid fixture specification {path}: {error}") from error


def specification_hash(spec: FixtureSpec) -> str:
    canonical = json.dumps(spec.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _load_csv[ModelT: BaseModel](
    path: Path, model: type[ModelT], expected_fields: list[str]
) -> tuple[ModelT, ...]:
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != expected_fields:
                raise DataValidationError(
                    f"{path.name} columns must be {expected_fields}; found {reader.fieldnames}"
                )
            rows: list[ModelT] = []
            for line_number, row in enumerate(reader, start=2):
                cleaned: dict[str, Any] = {
                    key: (None if value == "" and key == "unit_cost" else value)
                    for key, value in row.items()
                }
                try:
                    rows.append(model.model_validate(cleaned))
                except ValidationError as error:
                    raise DataValidationError(
                        f"invalid {path.name} row {line_number}: {error}"
                    ) from error
            return tuple(rows)
    except OSError as error:
        raise DataValidationError(f"unable to read {path}: {error}") from error


def _assert_unique(name: str, keys: list[tuple[object, ...]]) -> None:
    if len(keys) != len(set(keys)):
        raise DataValidationError(f"{name} contains duplicate natural keys")


def validate_bundle(bundle: FixtureBundle) -> None:
    spec = bundle.spec
    instrument_ids = {item.instrument_id for item in bundle.instruments}
    expected_ids = {item.instrument_id for item in spec.instruments}
    if instrument_ids != expected_ids:
        raise DataValidationError("instrument CSV does not match the specification")

    _assert_unique("instruments.csv", [(item.instrument_id,) for item in bundle.instruments])
    _assert_unique(
        "positions.csv",
        [(item.portfolio_id, item.effective_date, item.instrument_id) for item in bundle.positions],
    )
    _assert_unique("prices.csv", [(item.date, item.instrument_id) for item in bundle.prices])
    _assert_unique(
        "fx_rates.csv",
        [(item.date, item.base_currency, item.quote_currency) for item in bundle.fx_rates],
    )

    for position in bundle.positions:
        if position.instrument_id not in instrument_ids:
            raise DataValidationError(f"unknown position instrument: {position.instrument_id}")
        if position.quantity < 0 and not spec.allow_short_positions:
            raise DataValidationError("short positions are disabled by the fixture specification")
    for price in bundle.prices:
        if price.instrument_id not in instrument_ids:
            raise DataValidationError(f"unknown price instrument: {price.instrument_id}")

    expected_dates = {item.date() for item in pd.bdate_range(spec.start_date, spec.end_date)}
    expected_price_keys = {
        (day, instrument_id) for day in expected_dates for instrument_id in instrument_ids
    }
    actual_price_keys = {(item.date, item.instrument_id) for item in bundle.prices}
    if actual_price_keys != expected_price_keys:
        missing = len(expected_price_keys - actual_price_keys)
        extra = len(actual_price_keys - expected_price_keys)
        raise DataValidationError(f"price coverage mismatch: {missing} missing, {extra} extra")

    required_currencies = {item.quote_currency for item in bundle.instruments} | {
        spec.base_currency
    }
    expected_fx_keys = {
        (day, spec.base_currency, currency)
        for day in expected_dates
        for currency in required_currencies
    }
    actual_fx_keys = {
        (item.date, item.base_currency, item.quote_currency) for item in bundle.fx_rates
    }
    if actual_fx_keys != expected_fx_keys:
        missing = len(expected_fx_keys - actual_fx_keys)
        extra = len(actual_fx_keys - expected_fx_keys)
        raise DataValidationError(f"FX coverage mismatch: {missing} missing, {extra} extra")
    if any(item.quote_currency is Currency.EUR and item.rate != 1.0 for item in bundle.fx_rates):
        raise DataValidationError("EUR/EUR FX rates must always equal 1.0")


def load_fixture_bundle(data_directory: Path) -> FixtureBundle:
    spec = load_specification(data_directory / "fixture-spec.toml")
    bundle = FixtureBundle(
        spec=spec,
        instruments=_load_csv(
            data_directory / "instruments.csv",
            Instrument,
            [
                "instrument_id",
                "display_name",
                "asset_class",
                "quote_currency",
                "price_multiplier",
                "factor_classification",
            ],
        ),
        positions=_load_csv(
            data_directory / "positions.csv",
            Position,
            ["portfolio_id", "effective_date", "instrument_id", "quantity", "unit_cost"],
        ),
        prices=_load_csv(data_directory / "prices.csv", Price, ["date", "instrument_id", "close"]),
        fx_rates=_load_csv(
            data_directory / "fx_rates.csv",
            FxRate,
            ["date", "base_currency", "quote_currency", "rate"],
        ),
        specification_hash=specification_hash(spec),
    )
    validate_bundle(bundle)
    return bundle
