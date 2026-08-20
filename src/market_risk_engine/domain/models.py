"""Public schemas and validation rules for synthetic market-risk data."""

from __future__ import annotations

import math
from datetime import date, datetime
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AssetClass(StrEnum):
    EQUITY = "equity"
    GOVERNMENT_BOND = "government_bond"
    CORPORATE_CREDIT = "corporate_credit"
    COMMODITY = "commodity"
    CASH = "cash"


class Currency(StrEnum):
    EUR = "EUR"
    USD = "USD"
    GBP = "GBP"


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, frozen=True)


def _require_text(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError("must not be blank")
    return stripped


class Instrument(StrictModel):
    instrument_id: str
    display_name: str
    asset_class: AssetClass
    quote_currency: Currency
    price_multiplier: float = Field(gt=0)
    factor_classification: str | None = None

    @field_validator("instrument_id", "display_name")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        return _require_text(value)

    @field_validator("factor_classification")
    @classmethod
    def validate_optional_text(cls, value: str | None) -> str | None:
        return _require_text(value) if value is not None else None


class Position(StrictModel):
    portfolio_id: str
    effective_date: date
    instrument_id: str
    quantity: float
    unit_cost: float | None = Field(default=None, gt=0)

    @field_validator("portfolio_id", "instrument_id")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        return _require_text(value)

    @field_validator("quantity")
    @classmethod
    def validate_nonzero_quantity(cls, value: float) -> float:
        if not math.isfinite(value) or value == 0:
            raise ValueError("quantity must be finite and non-zero")
        return value


class Price(StrictModel):
    date: date
    instrument_id: str
    close: float = Field(gt=0)

    @field_validator("instrument_id")
    @classmethod
    def validate_instrument_id(cls, value: str) -> str:
        return _require_text(value)


class FxRate(StrictModel):
    date: date
    base_currency: Currency
    quote_currency: Currency
    rate: float = Field(gt=0)


class SyntheticInstrumentSpec(Instrument):
    initial_price: float = Field(gt=0)
    annual_return: float
    annual_volatility: float = Field(gt=0)
    factor_loadings: dict[str, float]

    @field_validator("annual_return", "annual_volatility")
    @classmethod
    def validate_finite_assumption(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("statistical assumptions must be finite")
        return value


class SyntheticFxSpec(StrictModel):
    quote_currency: Currency
    initial_rate: float = Field(gt=0)
    annual_return: float
    annual_volatility: float = Field(gt=0)
    factor_loadings: dict[str, float]

    @model_validator(mode="after")
    def reject_eur_series(self) -> Self:
        if self.quote_currency is Currency.EUR:
            raise ValueError("EUR/EUR is generated as the constant 1.0 series")
        return self


class FixtureSpec(StrictModel):
    specification_version: str
    seed: int = Field(ge=0)
    portfolio_id: str
    base_currency: Currency
    start_date: date
    end_date: date
    position_date: date
    trading_days_per_year: int = Field(default=252, gt=0)
    allow_short_positions: bool = False
    factors: list[str]
    instruments: list[SyntheticInstrumentSpec]
    fx_series: list[SyntheticFxSpec]
    positions: list[Position]

    @field_validator("specification_version", "portfolio_id")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        return _require_text(value)

    @field_validator("factors")
    @classmethod
    def validate_factors(cls, values: list[str]) -> list[str]:
        cleaned = [_require_text(value) for value in values]
        if not cleaned or len(cleaned) != len(set(cleaned)):
            raise ValueError("factors must be non-empty and unique")
        return cleaned

    @model_validator(mode="after")
    def validate_cross_references(self) -> Self:
        if self.base_currency is not Currency.EUR:
            raise ValueError("the public fixture base currency must be EUR")
        if self.start_date >= self.end_date:
            raise ValueError("start_date must be before end_date")
        if not self.start_date <= self.position_date <= self.end_date:
            raise ValueError("position_date must be within the fixture date range")
        instrument_ids = [item.instrument_id for item in self.instruments]
        if len(instrument_ids) != len(set(instrument_ids)):
            raise ValueError("instrument_id values must be unique")
        known_ids = set(instrument_ids)
        position_keys: set[tuple[str, date, str]] = set()
        for position in self.positions:
            if position.instrument_id not in known_ids:
                raise ValueError(f"unknown position instrument: {position.instrument_id}")
            if position.quantity < 0 and not self.allow_short_positions:
                raise ValueError("short positions require allow_short_positions=true")
            key = (position.portfolio_id, position.effective_date, position.instrument_id)
            if key in position_keys:
                raise ValueError(f"duplicate position key: {key}")
            position_keys.add(key)
            if position.portfolio_id != self.portfolio_id:
                raise ValueError("all positions must use the configured portfolio_id")
        factor_set = set(self.factors)
        all_loadings = [item.factor_loadings for item in self.instruments]
        all_loadings.extend(item.factor_loadings for item in self.fx_series)
        for loadings in all_loadings:
            unknown = set(loadings) - factor_set
            if unknown:
                raise ValueError(f"unknown factor loadings: {sorted(unknown)}")
            if sum(value * value for value in loadings.values()) >= 1:
                raise ValueError("squared factor loadings must sum to less than 1")
        required_fx = {
            item.quote_currency
            for item in self.instruments
            if item.quote_currency is not Currency.EUR
        }
        configured_fx = {item.quote_currency for item in self.fx_series}
        if required_fx != configured_fx or len(configured_fx) != len(self.fx_series):
            raise ValueError("FX specifications must uniquely cover all non-EUR currencies")
        return self


class CalculationRun(StrictModel):
    run_id: str
    portfolio_id: str
    status: RunStatus
    fixture_version: str
    random_seed: int = Field(ge=0)
    base_currency: Currency
    input_data_cutoff: date
    effective_position_date: date
    package_version: str
    requested_at: datetime
    completed_at: datetime | None = None
    failure_reason: str | None = None

    @field_validator("run_id", "portfolio_id", "fixture_version", "package_version")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        return _require_text(value)
