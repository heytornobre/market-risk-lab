from pathlib import Path

import pytest

from market_risk_engine.data.loaders import FixtureBundle, load_fixture_bundle


@pytest.fixture(scope="session")
def fixture_directory() -> Path:
    return Path(__file__).parents[1] / "data" / "synthetic"


@pytest.fixture(scope="session")
def fixture_bundle(fixture_directory: Path) -> FixtureBundle:
    return load_fixture_bundle(fixture_directory)
