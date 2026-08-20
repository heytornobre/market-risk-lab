import hashlib
import sqlite3
from pathlib import Path

import pytest

from market_risk_engine.dashboard.data import (
    list_succeeded_runs,
    load_dashboard_data,
    risk_comparison_rows,
    validate_existing_database,
)
from market_risk_engine.domain.models import RunStatus
from market_risk_engine.exceptions import RepositoryError
from market_risk_engine.risk.models import CalculationRequest
from market_risk_engine.risk.service import CalculationService
from market_risk_engine.storage.sqlite import SQLiteRepository


def _request(scenario_path: Path, *, risk_free_rate: float | None = None) -> CalculationRequest:
    return CalculationRequest(
        portfolio_id="SYNTHETIC_LAB_PORTFOLIO",
        as_of_date="2025-12-31",
        confidence_levels=(0.95,),
        horizons=(1,),
        monte_carlo_seed=123,
        monte_carlo_simulations=1_000,
        benchmark_instrument_id="LAB_EQ_EUR_A",
        scenario_set_path=str(scenario_path),
        annual_risk_free_rate=risk_free_rate,
    )


def _completed_database(
    database: Path, fixture_bundle: object, fixture_directory: Path
) -> SQLiteRepository:
    repository = SQLiteRepository(database)
    repository.migrate()
    repository.load_fixture(fixture_bundle)  # type: ignore[arg-type]
    request = _request(fixture_directory / "stress-scenarios.toml")
    CalculationService(repository, run_id_factory=lambda: "run-a").run(request)
    CalculationService(repository, run_id_factory=lambda: "run-b").run(request)
    spec = fixture_bundle.spec  # type: ignore[attr-defined]
    repository.create_calculation_run(
        run_id="run-pending",
        portfolio_id=spec.portfolio_id,
        fixture_version=spec.specification_version,
        random_seed=1,
        base_currency=spec.base_currency,
        input_data_cutoff=spec.end_date,
        effective_position_date=spec.position_date,
    )
    repository.create_calculation_run(
        run_id="run-failed",
        portfolio_id=spec.portfolio_id,
        fixture_version=spec.specification_version,
        random_seed=1,
        base_currency=spec.base_currency,
        input_data_cutoff=spec.end_date,
        effective_position_date=spec.position_date,
    )
    repository.transition_run("run-failed", RunStatus.FAILED, failure_reason="synthetic failure")
    return repository


def test_dashboard_reads_are_read_only_and_select_succeeded_runs(
    tmp_path: Path, fixture_bundle: object, fixture_directory: Path
) -> None:
    database = tmp_path / "risk.db"
    _completed_database(database, fixture_bundle, fixture_directory)
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    runs = list_succeeded_runs(database)
    latest = load_dashboard_data(database)
    explicit = load_dashboard_data(database, "run-a")
    after = hashlib.sha256(database.read_bytes()).hexdigest()

    assert [run.run_id for run in runs] == ["run-b", "run-a"]
    assert latest.run["run_id"] == "run-b"
    assert explicit.run["run_id"] == "run-a"
    assert after == before
    assert not Path(f"{database}-wal").exists()
    assert not Path(f"{database}-journal").exists()


def test_risk_view_model_keeps_parametric_cvar_unavailable_and_converts_eur(
    tmp_path: Path, fixture_bundle: object, fixture_directory: Path
) -> None:
    database = tmp_path / "risk.db"
    _completed_database(database, fixture_bundle, fixture_directory)
    data = load_dashboard_data(database, "run-a")
    rows = risk_comparison_rows(data)
    unavailable = next(
        row for row in rows if row["Model"] == "Parametric" and row["Measure"] == "CVaR"
    )
    historical = next(
        row for row in rows if row["Model"] == "Historical" and row["Measure"] == "VaR"
    )
    persisted = next(row for row in data.risk_rows if row["method"] == "historical_var")

    assert unavailable["Loss %"] is None
    assert unavailable["Loss EUR"] is None
    assert historical["Loss EUR"] == pytest.approx(
        float(persisted["value"]) * data.portfolio_market_value_eur
    )


def test_stress_sign_coverage_and_optional_alpha(
    tmp_path: Path, fixture_bundle: object, fixture_directory: Path
) -> None:
    database = tmp_path / "risk.db"
    _completed_database(database, fixture_bundle, fixture_directory)
    data = load_dashboard_data(database)

    assert all(float(row["pnl_eur"]) < 0 for row in data.stress_rows)
    assert all(0 < float(row["coverage_ratio"]) <= 1 for row in data.stress_rows)
    assert "annualised_excess_return_alpha" not in {str(row["metric"]) for row in data.factor_rows}
    assert data.model_identity["annual_risk_free_rate"] is None


def test_missing_unmigrated_and_empty_database_errors(tmp_path: Path) -> None:
    missing = tmp_path / "missing" / "risk.db"
    with pytest.raises(RepositoryError, match="database not found"):
        validate_existing_database(missing)
    assert not missing.parent.exists()

    empty = tmp_path / "empty.db"
    empty.touch()
    with pytest.raises(RepositoryError, match="not migrated"):
        load_dashboard_data(empty)

    migrated = tmp_path / "migrated.db"
    SQLiteRepository(migrated).migrate()
    with pytest.raises(RepositoryError, match="no fixture"):
        load_dashboard_data(migrated)


def test_incomplete_succeeded_run_is_rejected(
    tmp_path: Path, fixture_bundle: object, fixture_directory: Path
) -> None:
    database = tmp_path / "risk.db"
    _completed_database(database, fixture_bundle, fixture_directory)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "DELETE FROM risk_results WHERE run_id = 'run-a' AND method = 'historical_cvar'"
        )
    with pytest.raises(RepositoryError, match="incomplete risk result set"):
        load_dashboard_data(database, "run-a")
