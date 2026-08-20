from datetime import date

import pytest
from pydantic import ValidationError

from market_risk_engine.domain.models import (
    AssetClass,
    Currency,
    FixtureSpec,
    Instrument,
    Position,
    Price,
)
from market_risk_engine.risk.models import CalculationRequest


def test_instrument_rejects_blank_identifier() -> None:
    with pytest.raises(ValidationError, match="must not be blank"):
        Instrument(
            instrument_id=" ",
            display_name="Fictional",
            asset_class=AssetClass.EQUITY,
            quote_currency=Currency.EUR,
            price_multiplier=1.0,
        )


@pytest.mark.parametrize("close", [0, -1, float("nan"), float("inf")])
def test_price_rejects_invalid_numbers(close: float) -> None:
    with pytest.raises(ValidationError):
        Price(date=date(2025, 1, 1), instrument_id="LAB_TEST", close=close)


def test_position_rejects_zero_quantity() -> None:
    with pytest.raises(ValidationError, match="non-zero"):
        Position(
            portfolio_id="LAB",
            effective_date=date(2025, 1, 1),
            instrument_id="LAB_TEST",
            quantity=0,
        )


def test_specification_rejects_short_without_policy(fixture_bundle: object) -> None:
    spec = fixture_bundle.spec  # type: ignore[attr-defined]
    payload = spec.model_dump(mode="json")
    payload["positions"][0]["quantity"] = -10
    with pytest.raises(ValidationError, match="short positions require"):
        FixtureSpec.model_validate(payload)


def test_specification_accepts_short_with_explicit_policy(fixture_bundle: object) -> None:
    spec = fixture_bundle.spec  # type: ignore[attr-defined]
    payload = spec.model_dump(mode="json")
    payload["allow_short_positions"] = True
    payload["positions"][0]["quantity"] = -10
    validated = FixtureSpec.model_validate(payload)
    assert validated.positions[0].quantity == -10


def test_specification_rejects_duplicate_instruments(fixture_bundle: object) -> None:
    spec = fixture_bundle.spec  # type: ignore[attr-defined]
    payload = spec.model_dump(mode="json")
    payload["instruments"].append(payload["instruments"][0])
    with pytest.raises(ValidationError, match="instrument_id values must be unique"):
        FixtureSpec.model_validate(payload)


def test_schema_rejects_unsupported_currency_and_asset_class() -> None:
    payload = {
        "instrument_id": "LAB_TEST",
        "display_name": "Fictional",
        "asset_class": "cryptoasset",
        "quote_currency": "JPY",
        "price_multiplier": 1,
    }
    with pytest.raises(ValidationError):
        Instrument.model_validate(payload)


def test_calculation_request_rejects_invalid_confidence() -> None:
    with pytest.raises(ValidationError, match="confidence levels"):
        CalculationRequest(
            portfolio_id="LAB",
            as_of_date="2025-01-01",
            confidence_levels=(1.0,),
            horizons=(1,),
            benchmark_instrument_id="LAB_BENCH",
            scenario_set_path="scenarios.toml",
        )
