import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from market_risk_engine.domain.models import Currency, RunStatus
from market_risk_engine.exceptions import RepositoryError
from market_risk_engine.storage.sqlite import SQLiteRepository

FIXED_TIME = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def _repository(path: Path) -> SQLiteRepository:
    return SQLiteRepository(path, clock=lambda: FIXED_TIME)


def test_migrations_are_idempotent(tmp_path: Path) -> None:
    repository = _repository(tmp_path / "risk.db")
    assert repository.migrate() == [1, 2]
    assert repository.migrate() == []
    assert repository.schema_versions() == [1, 2]


def test_fixture_loading_is_idempotent(tmp_path: Path, fixture_bundle: object) -> None:
    repository = _repository(tmp_path / "risk.db")
    repository.migrate()
    repository.load_fixture(fixture_bundle)  # type: ignore[arg-type]
    first = repository.inspect_fixture()
    repository.load_fixture(fixture_bundle)  # type: ignore[arg-type]
    assert repository.inspect_fixture() == first


def test_transaction_rolls_back_on_foreign_key_failure(tmp_path: Path) -> None:
    repository = _repository(tmp_path / "risk.db")
    repository.migrate()
    with pytest.raises(sqlite3.IntegrityError), repository.transaction() as connection:
        connection.execute(
            """
            INSERT INTO instruments VALUES
            ('LAB_TEMP', 'Temporary', 'cash', 'EUR', 1.0, NULL)
            """
        )
        connection.execute(
            "INSERT INTO prices(date, instrument_id, close) VALUES ('2025-01-01','UNKNOWN',1)"
        )
    assert repository.instruments() == []


def test_calculation_run_transitions(tmp_path: Path, fixture_bundle: object) -> None:
    repository = _repository(tmp_path / "risk.db")
    repository.migrate()
    repository.load_fixture(fixture_bundle)  # type: ignore[arg-type]
    spec = fixture_bundle.spec  # type: ignore[attr-defined]
    run = repository.create_calculation_run(
        run_id="run-001",
        portfolio_id=spec.portfolio_id,
        fixture_version=spec.specification_version,
        random_seed=spec.seed,
        base_currency=Currency.EUR,
        input_data_cutoff=spec.end_date,
        effective_position_date=spec.position_date,
    )
    assert run.status is RunStatus.PENDING
    assert repository.transition_run("run-001", RunStatus.RUNNING).status is RunStatus.RUNNING
    completed = repository.transition_run("run-001", RunStatus.SUCCEEDED)
    assert completed.status is RunStatus.SUCCEEDED
    assert completed.completed_at == FIXED_TIME
    with pytest.raises(RepositoryError, match="invalid run transition"):
        repository.transition_run("run-001", RunStatus.FAILED, failure_reason="late failure")


def test_failed_run_requires_reason(tmp_path: Path, fixture_bundle: object) -> None:
    repository = _repository(tmp_path / "risk.db")
    repository.migrate()
    repository.load_fixture(fixture_bundle)  # type: ignore[arg-type]
    spec = fixture_bundle.spec  # type: ignore[attr-defined]
    repository.create_calculation_run(
        run_id="run-002",
        portfolio_id=spec.portfolio_id,
        fixture_version=spec.specification_version,
        random_seed=spec.seed,
        base_currency=Currency.EUR,
        input_data_cutoff=spec.end_date,
        effective_position_date=spec.position_date,
    )
    with pytest.raises(RepositoryError, match="failure reason"):
        repository.transition_run("run-002", RunStatus.FAILED)


def test_risk_result_identity_constraint(tmp_path: Path, fixture_bundle: object) -> None:
    repository = _repository(tmp_path / "risk.db")
    repository.migrate()
    repository.load_fixture(fixture_bundle)  # type: ignore[arg-type]
    spec = fixture_bundle.spec  # type: ignore[attr-defined]
    repository.create_calculation_run(
        run_id="run-003",
        portfolio_id=spec.portfolio_id,
        fixture_version=spec.specification_version,
        random_seed=spec.seed,
        base_currency=Currency.EUR,
        input_data_cutoff=spec.end_date,
        effective_position_date=spec.position_date,
    )
    values = (
        "run-003",
        "reserved",
        "baseline",
        0.99,
        1,
        1.23,
        "0" * 64,
        "{}",
        "0.1.0",
        "2026-01-02T03:04:05Z",
    )
    with repository.transaction() as connection:
        connection.execute("INSERT INTO risk_results VALUES (?,?,?,?,?,?,?,?,?,?)", values)
    with pytest.raises(sqlite3.IntegrityError), repository.transaction() as connection:
        connection.execute("INSERT INTO risk_results VALUES (?,?,?,?,?,?,?,?,?,?)", values)
