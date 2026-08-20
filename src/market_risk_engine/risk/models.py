"""A4 calculation requests, results, and persistence records."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date
from typing import Any, Self

from pydantic import Field, field_validator, model_validator

from market_risk_engine.domain.models import StrictModel


def canonical_parameters(parameters: dict[str, Any]) -> tuple[str, str]:
    encoded = json.dumps(parameters, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class CalculationRequest(StrictModel):
    portfolio_id: str
    as_of_date: date
    confidence_levels: tuple[float, ...] = (0.95, 0.99)
    horizons: tuple[int, ...] = (1, 10)
    monte_carlo_seed: int = Field(default=20260815, ge=0)
    monte_carlo_simulations: int = Field(default=20_000, ge=1_000)
    benchmark_instrument_id: str
    scenario_set_path: str
    annual_risk_free_rate: float | None = Field(default=None, gt=-1)

    @field_validator("portfolio_id", "benchmark_instrument_id", "scenario_set_path")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @field_validator("confidence_levels")
    @classmethod
    def validate_confidence_levels(cls, values: tuple[float, ...]) -> tuple[float, ...]:
        if not values or any(not math.isfinite(value) or not 0 < value < 1 for value in values):
            raise ValueError("confidence levels must be finite values strictly between 0 and 1")
        if len(values) != len(set(values)):
            raise ValueError("confidence levels must be unique")
        return tuple(sorted(values))

    @field_validator("horizons")
    @classmethod
    def validate_horizons(cls, values: tuple[int, ...]) -> tuple[int, ...]:
        if not values or any(value <= 0 for value in values):
            raise ValueError("horizons must be positive business-day counts")
        if len(values) != len(set(values)):
            raise ValueError("horizons must be unique")
        return tuple(sorted(values))


class RiskResultRecord(StrictModel):
    method: str
    model_variant: str
    confidence_level: float
    horizon: int
    value: float
    parameter_hash: str
    model_parameters_json: str
    calculation_version: str


class StressResultRecord(StrictModel):
    scenario_id: str
    scenario_version: str
    portfolio_return: float
    pnl_eur: float
    covered_market_value_eur: float
    gross_market_value_eur: float
    coverage_ratio: float
    uncovered_instruments: tuple[str, ...]
    model_parameters_json: str


class FactorMetricRecord(StrictModel):
    benchmark_instrument_id: str
    metric: str
    value: float
    model_variant: str
    model_parameters_json: str


class CalculationOutput(StrictModel):
    run_id: str
    status: str
    effective_position_date: date
    portfolio_market_value_eur: float = Field(gt=0)
    risk_results: tuple[RiskResultRecord, ...]
    stress_results: tuple[StressResultRecord, ...]
    factor_metrics: tuple[FactorMetricRecord, ...]

    @model_validator(mode="after")
    def validate_completed_output(self) -> Self:
        if self.status != "succeeded":
            raise ValueError("calculation output must represent a succeeded run")
        return self
