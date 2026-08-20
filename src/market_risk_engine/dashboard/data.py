"""Read-only SQLite queries and deterministic dashboard view models."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from market_risk_engine.exceptions import RepositoryError

REQUIRED_RISK_METHODS = frozenset(
    {
        "historical_var",
        "historical_cvar",
        "parametric_var",
        "monte_carlo_var",
        "monte_carlo_cvar",
    }
)
REQUIRED_FACTOR_METRICS = frozenset(
    {"beta", "correlation", "annualised_volatility", "tracking_error"}
)


@dataclass(frozen=True)
class RunOption:
    run_id: str
    requested_at: str
    portfolio_id: str
    input_data_cutoff: str

    @property
    def label(self) -> str:
        return f"{self.input_data_cutoff} · {self.run_id[:8]}"


@dataclass(frozen=True)
class DashboardData:
    run: dict[str, Any]
    fixture: dict[str, Any]
    instrument_count: int
    portfolio_market_value_eur: float
    risk_rows: tuple[dict[str, Any], ...]
    stress_rows: tuple[dict[str, Any], ...]
    factor_rows: tuple[dict[str, Any], ...]
    failed_runs: tuple[dict[str, Any], ...]
    model_identity: dict[str, Any]


def validate_existing_database(database: Path) -> Path:
    """Require an existing regular database file without creating any path component."""
    if not database.is_file():
        raise RepositoryError(
            f"database not found: {database}; run 'db migrate' and 'demo load' first"
        )
    return database.resolve()


def _connect_read_only(database: Path) -> sqlite3.Connection:
    resolved = validate_existing_database(database)
    uri = f"file:{quote(str(resolved), safe='/')}?mode=ro&immutable=1"
    try:
        connection = sqlite3.connect(uri, uri=True, isolation_level=None)
    except sqlite3.Error as error:
        raise RepositoryError(f"unable to open database read-only: {error}") from error
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def _schema_guard(connection: sqlite3.Connection) -> None:
    try:
        versions = connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
    except sqlite3.Error as error:
        raise RepositoryError("database is not migrated; run 'db migrate'") from error
    if [int(row[0]) for row in versions] != [1, 2]:
        raise RepositoryError("database requires migrations 1 and 2; run 'db migrate'")


def list_succeeded_runs(database: Path) -> tuple[RunOption, ...]:
    with closing(_connect_read_only(database)) as connection:
        _schema_guard(connection)
        rows = connection.execute(
            """
            SELECT run_id, requested_at, portfolio_id, input_data_cutoff
            FROM calculation_runs
            WHERE status = 'succeeded'
            ORDER BY requested_at DESC, run_id DESC
            """
        ).fetchall()
    return tuple(RunOption(**dict(row)) for row in rows)


def _parse_json_object(value: str, label: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError) as error:
        raise RepositoryError(f"selected run contains invalid {label} provenance") from error
    if not isinstance(parsed, dict):
        raise RepositoryError(f"selected run contains invalid {label} provenance")
    return parsed


def _validate_complete_results(
    risk_rows: list[dict[str, Any]],
    stress_rows: list[dict[str, Any]],
    factor_rows: list[dict[str, Any]],
) -> None:
    if not risk_rows:
        raise RepositoryError("selected succeeded run has no risk results")
    dimensions = {(float(row["confidence_level"]), int(row["horizon"])) for row in risk_rows}
    for confidence, horizon in dimensions:
        methods = {
            str(row["method"])
            for row in risk_rows
            if float(row["confidence_level"]) == confidence and int(row["horizon"]) == horizon
        }
        if methods != REQUIRED_RISK_METHODS:
            missing = sorted(REQUIRED_RISK_METHODS - methods)
            extra = sorted(methods - REQUIRED_RISK_METHODS)
            raise RepositoryError(
                "selected succeeded run has an incomplete risk result set "
                f"for confidence={confidence:g}, horizon={horizon}: "
                f"missing={missing}, extra={extra}"
            )
    if not stress_rows:
        raise RepositoryError("selected succeeded run has no stress results")
    metrics = {str(row["metric"]) for row in factor_rows}
    if not REQUIRED_FACTOR_METRICS.issubset(metrics):
        raise RepositoryError(
            "selected succeeded run has incomplete factor metrics: "
            f"missing={sorted(REQUIRED_FACTOR_METRICS - metrics)}"
        )


def load_dashboard_data(database: Path, run_id: str | None = None) -> DashboardData:
    """Load one complete succeeded run and shape immutable presentation records."""
    with closing(_connect_read_only(database)) as connection:
        _schema_guard(connection)
        fixture_row = connection.execute(
            "SELECT * FROM fixture_loads ORDER BY specification_version DESC LIMIT 1"
        ).fetchone()
        if fixture_row is None:
            raise RepositoryError("no fixture is loaded; run 'demo load'")
        if run_id is None:
            run_row = connection.execute(
                """
                SELECT * FROM calculation_runs WHERE status = 'succeeded'
                ORDER BY requested_at DESC, run_id DESC LIMIT 1
                """
            ).fetchone()
        else:
            run_row = connection.execute(
                "SELECT * FROM calculation_runs WHERE run_id = ? AND status = 'succeeded'",
                (run_id,),
            ).fetchone()
        if run_row is None:
            message = (
                "no succeeded calculation run exists; run 'risk run'"
                if run_id is None
                else "selected succeeded calculation run no longer exists"
            )
            raise RepositoryError(message)
        selected = str(run_row["run_id"])
        risk_rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT method, model_variant, confidence_level, horizon, value,
                       parameter_hash, model_parameters_json, calculation_version
                FROM risk_results WHERE run_id = ?
                ORDER BY confidence_level, horizon, method
                """,
                (selected,),
            )
        ]
        stress_rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT scenario_id, scenario_version, portfolio_return, pnl_eur,
                       covered_market_value_eur, gross_market_value_eur, coverage_ratio,
                       uncovered_instruments_json, model_parameters_json
                FROM stress_results WHERE run_id = ? ORDER BY scenario_id
                """,
                (selected,),
            )
        ]
        factor_rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT benchmark_instrument_id, metric, value, model_variant,
                       model_parameters_json
                FROM factor_metrics WHERE run_id = ? ORDER BY metric
                """,
                (selected,),
            )
        ]
        failed_runs = [
            dict(row)
            for row in connection.execute(
                """
                SELECT run_id, requested_at, completed_at, failure_reason
                FROM calculation_runs WHERE status = 'failed'
                ORDER BY requested_at DESC, run_id DESC LIMIT 10
                """
            )
        ]
        instrument_count = int(connection.execute("SELECT COUNT(*) FROM instruments").fetchone()[0])

    _validate_complete_results(risk_rows, stress_rows, factor_rows)
    for row in stress_rows:
        uncovered = json.loads(str(row.pop("uncovered_instruments_json")))
        if not isinstance(uncovered, list) or not all(isinstance(item, str) for item in uncovered):
            raise RepositoryError("selected run contains invalid uncovered-instrument provenance")
        row["uncovered_instruments"] = tuple(uncovered)
    market_values = {float(row["gross_market_value_eur"]) for row in stress_rows}
    if len(market_values) != 1:
        raise RepositoryError("selected succeeded run has inconsistent portfolio market values")
    model_parameters = _parse_json_object(str(risk_rows[0]["model_parameters_json"]), "risk-model")
    factor_parameters = _parse_json_object(
        str(factor_rows[0]["model_parameters_json"]), "factor-model"
    )
    model_identity = {
        "parameter_hash": str(risk_rows[0]["parameter_hash"]),
        "calculation_version": str(risk_rows[0]["calculation_version"]),
        "quantile_method": model_parameters.get("quantile_method", "linear / model-specific"),
        "cvar_tail_method": model_parameters.get(
            "cvar_tail_method", "equal-probability fractional boundary"
        ),
        "benchmark": str(factor_rows[0]["benchmark_instrument_id"]),
        "annual_risk_free_rate": factor_parameters.get("annual_risk_free_rate"),
        "monte_carlo_seed": int(run_row["random_seed"]),
    }
    monte_carlo = next((row for row in risk_rows if str(row["method"]) == "monte_carlo_var"), None)
    if monte_carlo is not None:
        mc_parameters = _parse_json_object(str(monte_carlo["model_parameters_json"]), "Monte Carlo")
        model_identity["monte_carlo_simulations"] = mc_parameters.get("simulations")
    scenario_versions = sorted({str(row["scenario_version"]) for row in stress_rows})
    model_identity["stress_scenario_version"] = ", ".join(scenario_versions)
    return DashboardData(
        run=dict(run_row),
        fixture=dict(fixture_row),
        instrument_count=instrument_count,
        portfolio_market_value_eur=market_values.pop(),
        risk_rows=tuple(risk_rows),
        stress_rows=tuple(stress_rows),
        factor_rows=tuple(factor_rows),
        failed_runs=tuple(failed_runs),
        model_identity=model_identity,
    )


def risk_comparison_rows(data: DashboardData) -> tuple[dict[str, Any], ...]:
    """Return chart/table rows, retaining absent parametric CVaR as explicit None."""
    lookup = {
        (str(row["method"]), float(row["confidence_level"]), int(row["horizon"])): float(
            row["value"]
        )
        for row in data.risk_rows
    }
    dimensions = sorted(
        {(float(row["confidence_level"]), int(row["horizon"])) for row in data.risk_rows}
    )
    labels = (
        ("Historical", "VaR", "historical_var"),
        ("Historical", "CVaR", "historical_cvar"),
        ("Parametric", "VaR", "parametric_var"),
        ("Parametric", "CVaR", "parametric_cvar"),
        ("Monte Carlo", "VaR", "monte_carlo_var"),
        ("Monte Carlo", "CVaR", "monte_carlo_cvar"),
    )
    rows: list[dict[str, Any]] = []
    for confidence, horizon in dimensions:
        for model, measure, method in labels:
            value = lookup.get((method, confidence, horizon))
            rows.append(
                {
                    "Label": f"{confidence:.0%} · {horizon}d",
                    "Model": model,
                    "Measure": measure,
                    "Loss %": None if value is None else value * 100.0,
                    "Loss EUR": None if value is None else value * data.portfolio_market_value_eur,
                }
            )
    return tuple(rows)
