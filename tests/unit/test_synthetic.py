import hashlib
import shutil
from pathlib import Path

import numpy as np
import pytest

from market_risk_engine.data.fx import base_currency_prices
from market_risk_engine.data.loaders import load_fixture_bundle, load_specification
from market_risk_engine.data.synthetic import build_correlation_matrix, generate_fixtures
from market_risk_engine.exceptions import DataValidationError


def _copy_fixture(source: Path, destination: Path) -> None:
    shutil.copytree(source, destination)


def test_correlation_construction_is_psd(fixture_directory: Path) -> None:
    spec = load_specification(fixture_directory / "fixture-spec.toml")
    labels, correlation = build_correlation_matrix(spec)
    assert len(labels) == 15
    assert np.allclose(np.diag(correlation), 1.0)
    assert np.linalg.eigvalsh(correlation).min() >= -1e-12


def test_generation_is_byte_reproducible(fixture_directory: Path, tmp_path: Path) -> None:
    spec = load_specification(fixture_directory / "fixture-spec.toml")
    first = tmp_path / "first"
    second = tmp_path / "second"
    first_hashes = generate_fixtures(spec, first)
    second_hashes = generate_fixtures(spec, second)
    assert first_hashes == second_hashes
    for filename in first_hashes:
        assert (first / filename).read_bytes() == (second / filename).read_bytes()


def test_committed_fixtures_match_generator(fixture_directory: Path, tmp_path: Path) -> None:
    spec = load_specification(fixture_directory / "fixture-spec.toml")
    hashes = generate_fixtures(spec, tmp_path)
    for filename, expected_hash in hashes.items():
        actual = hashlib.sha256((fixture_directory / filename).read_bytes()).hexdigest()
        assert actual == expected_hash


def test_bundle_has_full_semantic_coverage(fixture_bundle: object) -> None:
    bundle = fixture_bundle
    assert len(bundle.instruments) == 13  # type: ignore[attr-defined]
    assert len(bundle.positions) == 13  # type: ignore[attr-defined]
    assert {item.quote_currency.value for item in bundle.instruments} == {  # type: ignore[attr-defined]
        "EUR",
        "USD",
        "GBP",
    }


def test_duplicate_price_key_is_rejected(fixture_directory: Path, tmp_path: Path) -> None:
    target = tmp_path / "fixture"
    _copy_fixture(fixture_directory, target)
    price_path = target / "prices.csv"
    lines = price_path.read_text().splitlines()
    price_path.write_text("\n".join([*lines, lines[1]]) + "\n")
    with pytest.raises(DataValidationError, match="duplicate natural keys"):
        load_fixture_bundle(target)


def test_malformed_numeric_value_is_rejected(fixture_directory: Path, tmp_path: Path) -> None:
    target = tmp_path / "fixture"
    _copy_fixture(fixture_directory, target)
    price_path = target / "prices.csv"
    content = price_path.read_text()
    price_path.write_text(content.replace(",100.0000000000", ",not-a-number", 1))
    with pytest.raises(DataValidationError, match="invalid prices.csv row"):
        load_fixture_bundle(target)


def test_missing_price_coverage_is_rejected(fixture_directory: Path, tmp_path: Path) -> None:
    target = tmp_path / "fixture"
    _copy_fixture(fixture_directory, target)
    price_path = target / "prices.csv"
    lines = price_path.read_text().splitlines()
    price_path.write_text("\n".join([lines[0], *lines[2:]]) + "\n")
    with pytest.raises(DataValidationError, match="price coverage mismatch"):
        load_fixture_bundle(target)


def test_missing_fx_coverage_is_rejected(fixture_directory: Path, tmp_path: Path) -> None:
    target = tmp_path / "fixture"
    _copy_fixture(fixture_directory, target)
    fx_path = target / "fx_rates.csv"
    lines = fx_path.read_text().splitlines()
    fx_path.write_text("\n".join([lines[0], *lines[2:]]) + "\n")
    with pytest.raises(DataValidationError, match="FX coverage mismatch"):
        load_fixture_bundle(target)


def test_unknown_price_instrument_is_rejected(fixture_directory: Path, tmp_path: Path) -> None:
    target = tmp_path / "fixture"
    _copy_fixture(fixture_directory, target)
    price_path = target / "prices.csv"
    lines = price_path.read_text().splitlines()
    lines[1] = lines[1].replace("LAB_CASH_EUR", "LAB_UNKNOWN")
    price_path.write_text("\n".join(lines) + "\n")
    with pytest.raises(DataValidationError, match="unknown price instrument"):
        load_fixture_bundle(target)


def test_historical_fx_conversion_uses_same_date_rate(fixture_bundle: object) -> None:
    bundle = fixture_bundle
    converted = base_currency_prices(bundle)  # type: ignore[arg-type]
    usd_instrument = next(
        item
        for item in bundle.instruments
        if item.quote_currency.value == "USD"  # type: ignore[attr-defined]
    )
    price = next(
        item
        for item in bundle.prices
        if item.instrument_id == usd_instrument.instrument_id  # type: ignore[attr-defined]
    )
    rate = next(
        item
        for item in bundle.fx_rates  # type: ignore[attr-defined]
        if item.date == price.date and item.quote_currency.value == "USD"
    )
    assert converted[(price.date, price.instrument_id)] == pytest.approx(price.close * rate.rate)
