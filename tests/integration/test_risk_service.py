from pathlib import Path

import pytest

from market_risk_engine.domain.models import RunStatus
from market_risk_engine.exceptions import DataValidationError, MarketRiskLabError, RepositoryError
from market_risk_engine.risk.models import CalculationRequest
from market_risk_engine.risk.service import CalculationService
from market_risk_engine.storage.migrations import MIGRATIONS
from market_risk_engine.storage.sqlite import SQLiteRepository


def _loaded_repository(path: Path, fixture_bundle: object) -> SQLiteRepository:
    repository = SQLiteRepository(path)
    repository.migrate()
    repository.load_fixture(fixture_bundle)  # type: ignore[arg-type]
    return repository


def _request(scenario_path: Path) -> CalculationRequest:
    return CalculationRequest(
        portfolio_id="SYNTHETIC_LAB_PORTFOLIO",
        as_of_date="2025-12-31",
        confidence_levels=(0.95,),
        horizons=(1,),
        monte_carlo_seed=123,
        monte_carlo_simulations=1_000,
        benchmark_instrument_id="LAB_EQ_EUR_A",
        scenario_set_path=str(scenario_path),
    )


def test_migration_2_upgrades_database_with_migration_1_applied(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "risk.db")
    with repository.transaction() as connection:
        connection.execute(
            "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        for statement in MIGRATIONS[1]:
            connection.execute(statement)
        connection.execute("INSERT INTO schema_migrations VALUES (1, '2026-01-01T00:00:00Z')")
    assert repository.migrate() == [2]
    assert repository.schema_versions() == [1, 2]
    with repository.connect() as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert {"stress_results", "factor_metrics", "risk_results"} <= tables


def test_equivalent_runs_have_identical_metrics(
    tmp_path: Path, fixture_bundle: object, fixture_directory: Path
) -> None:
    repository = _loaded_repository(tmp_path / "risk.db", fixture_bundle)
    request = _request(fixture_directory / "stress-scenarios.toml")
    first = CalculationService(repository, run_id_factory=lambda: "run-a").run(request)
    second = CalculationService(repository, run_id_factory=lambda: "run-b").run(request)
    assert [item.model_dump(exclude={"parameter_hash"}) for item in first.risk_results] == [
        item.model_dump(exclude={"parameter_hash"}) for item in second.risk_results
    ]
    assert first.stress_results == second.stress_results
    assert first.factor_metrics == second.factor_metrics


def test_persistence_failure_rolls_back_results_and_marks_failed(
    tmp_path: Path, fixture_bundle: object, fixture_directory: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _loaded_repository(tmp_path / "risk.db", fixture_bundle)

    def failing_commit(run_id: str, **kwargs: object) -> None:
        result = kwargs["risk_results"][0]  # type: ignore[index]
        with repository.transaction() as connection:
            connection.execute(
                """
                INSERT INTO risk_results VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    run_id,
                    result.method,
                    result.model_variant,
                    result.confidence_level,
                    result.horizon,
                    result.value,
                    result.parameter_hash,
                    result.model_parameters_json,
                    result.calculation_version,
                    "2026-01-01T00:00:00Z",
                ),
            )
            raise RepositoryError("injected persistence failure")

    monkeypatch.setattr(repository, "commit_calculation_results", failing_commit)
    service = CalculationService(repository, run_id_factory=lambda: "run-failed")
    with pytest.raises(MarketRiskLabError, match="injected persistence failure"):
        service.run(_request(fixture_directory / "stress-scenarios.toml"))
    inspected = repository.inspect_calculation("run-failed")
    assert inspected["run"]["status"] == "failed"
    assert inspected["risk_results"] == []
    assert inspected["stress_results"] == []
    assert inspected["factor_metrics"] == []


def test_missing_scenario_file_records_failed_run(tmp_path: Path, fixture_bundle: object) -> None:
    repository = _loaded_repository(tmp_path / "risk.db", fixture_bundle)
    service = CalculationService(repository, run_id_factory=lambda: "run-missing-scenario")
    with pytest.raises(MarketRiskLabError) as captured:
        service.run(_request(tmp_path / "missing.toml"))
    run = repository.inspect_calculation("run-missing-scenario")["run"]
    assert run["status"] == "failed"
    assert str(tmp_path) not in run["failure_reason"]
    assert "DataValidationError" in run["failure_reason"]
    assert "invalid stress scenario set <local-path>/missing.toml" in run["failure_reason"]
    assert "No such file or directory" in run["failure_reason"]
    assert str(tmp_path) not in str(captured.value)
    assert "invalid stress scenario set <local-path>/missing.toml" in str(captured.value)


@pytest.mark.parametrize(
    "path",
    [
        "/" + "tmp" + "/ci-job/missing.toml",
        "/private/" + "tmp" + "/ci-job/missing.toml",
        "/" + "home" + "/runner/work/missing.toml",
        "/" + "Users" + "/example/work/missing.toml",
        "C:" + "\\Users" + "\\example\\work\\missing.toml",
        "C:" + "\\Windows\\Temp\\ci-job\\missing.toml",
    ],
)
def test_failure_reason_redacts_platform_local_paths(path: str) -> None:
    from market_risk_engine.risk.service import _safe_failure_reason

    reason = _safe_failure_reason(DataValidationError(f"could not read {path}: unavailable"))
    assert path not in reason
    assert reason == "DataValidationError: could not read <local-path>/missing.toml: unavailable"


def test_risk_identity_allows_distinct_parameter_variants(
    tmp_path: Path, fixture_bundle: object
) -> None:
    repository = _loaded_repository(tmp_path / "risk.db", fixture_bundle)
    spec = fixture_bundle.spec  # type: ignore[attr-defined]
    repository.create_calculation_run(
        run_id="run-identity",
        portfolio_id=spec.portfolio_id,
        fixture_version=spec.specification_version,
        random_seed=1,
        base_currency=spec.base_currency,
        input_data_cutoff=spec.end_date,
        effective_position_date=spec.position_date,
    )
    repository.transition_run("run-identity", RunStatus.RUNNING)
    with repository.transaction() as connection:
        base = (
            "run-identity",
            "monte_carlo_var",
            "variant",
            0.95,
            1,
            0.1,
            "a" * 64,
            '{"seed":1}',
            "0.1.0",
            "2026-01-01T00:00:00Z",
        )
        connection.execute("INSERT INTO risk_results VALUES (?,?,?,?,?,?,?,?,?,?)", base)
        changed = (*base[:6], "b" * 64, '{"seed":2}', *base[8:])
        connection.execute("INSERT INTO risk_results VALUES (?,?,?,?,?,?,?,?,?,?)", changed)
        count = connection.execute(
            "SELECT COUNT(*) FROM risk_results WHERE run_id='run-identity'"
        ).fetchone()[0]
    assert count == 2
