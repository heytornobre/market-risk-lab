"""Original synthetic stress scenarios with explicit coverage and shock composition."""

from __future__ import annotations

import math
import tomllib
from pathlib import Path
from typing import Self

from pydantic import Field, field_validator, model_validator

from market_risk_engine.domain.models import AssetClass, Currency, StrictModel
from market_risk_engine.exceptions import DataValidationError
from market_risk_engine.risk.models import StressResultRecord, canonical_parameters
from market_risk_engine.risk.portfolio import PortfolioData


class StressScenario(StrictModel):
    scenario_id: str
    version: str
    description: str
    asset_class_shocks: dict[AssetClass, float] = Field(default_factory=dict)
    currency_shocks: dict[Currency, float] = Field(default_factory=dict)
    instrument_shocks: dict[str, float] = Field(default_factory=dict)

    @field_validator("scenario_id", "version", "description")
    @classmethod
    def validate_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @model_validator(mode="after")
    def validate_shocks(self) -> Self:
        shocks = [
            *self.asset_class_shocks.values(),
            *self.currency_shocks.values(),
            *self.instrument_shocks.values(),
        ]
        if not shocks:
            raise ValueError("scenario must define at least one shock")
        if any(not math.isfinite(value) or value < -1 for value in shocks):
            raise ValueError("scenario shocks must be finite and no lower than -100%")
        return self


class StressScenarioSet(StrictModel):
    scenario_set_id: str
    version: str
    scenarios: tuple[StressScenario, ...]

    @model_validator(mode="after")
    def validate_unique_scenarios(self) -> Self:
        keys = [(item.scenario_id, item.version) for item in self.scenarios]
        if not keys or len(keys) != len(set(keys)):
            raise ValueError("scenario identifiers and versions must be non-empty and unique")
        return self


def load_scenario_set(path: Path) -> StressScenarioSet:
    try:
        return StressScenarioSet.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))
    except (OSError, tomllib.TOMLDecodeError, ValueError) as error:
        raise DataValidationError(f"invalid stress scenario set {path}: {error}") from error


def apply_scenario(data: PortfolioData, scenario: StressScenario) -> StressResultRecord:
    unknown_mappings = set(scenario.instrument_shocks) - set(data.held_instrument_ids)
    if unknown_mappings:
        raise DataValidationError(
            f"scenario {scenario.scenario_id} maps unknown portfolio instruments: "
            f"{sorted(unknown_mappings)}"
        )
    pnl = 0.0
    covered_value = 0.0
    uncovered: list[str] = []
    applied: dict[str, dict[str, float]] = {}
    for instrument_id, market_value in zip(
        data.held_instrument_ids, data.market_values_eur, strict=True
    ):
        instrument = data.instruments[instrument_id]
        has_asset_shock = instrument.asset_class in scenario.asset_class_shocks
        has_instrument_shock = instrument_id in scenario.instrument_shocks
        if has_asset_shock and has_instrument_shock:
            raise DataValidationError(
                f"scenario {scenario.scenario_id} has conflicting local shock mappings for "
                f"{instrument_id}"
            )
        local_shock = (
            scenario.instrument_shocks[instrument_id]
            if has_instrument_shock
            else scenario.asset_class_shocks.get(instrument.asset_class)
        )
        fx_shock = scenario.currency_shocks.get(instrument.quote_currency)
        if local_shock is None and fx_shock is None:
            uncovered.append(instrument_id)
            continue
        local = local_shock or 0.0
        fx = fx_shock or 0.0
        combined_return = (1.0 + local) * (1.0 + fx) - 1.0
        covered_value += float(abs(market_value))
        pnl += float(market_value) * combined_return
        applied[instrument_id] = {
            "local_shock": local,
            "fx_shock": fx,
            "combined_return": combined_return,
        }
    if covered_value <= 0:
        raise DataValidationError(
            f"scenario {scenario.scenario_id} has zero covered portfolio exposure"
        )
    coverage_ratio = covered_value / data.gross_market_value_eur
    parameters, _ = canonical_parameters(
        {
            "combination": "multiplicative",
            "loss_sign": "negative_pnl",
            "applied_shocks": applied,
        }
    )
    return StressResultRecord(
        scenario_id=scenario.scenario_id,
        scenario_version=scenario.version,
        portfolio_return=pnl / data.gross_market_value_eur,
        pnl_eur=pnl,
        covered_market_value_eur=covered_value,
        gross_market_value_eur=data.gross_market_value_eur,
        coverage_ratio=coverage_ratio,
        uncovered_instruments=tuple(sorted(uncovered)),
        model_parameters_json=parameters,
    )


def run_scenarios(
    data: PortfolioData, scenario_set: StressScenarioSet
) -> tuple[StressResultRecord, ...]:
    return tuple(apply_scenario(data, item) for item in scenario_set.scenarios)
