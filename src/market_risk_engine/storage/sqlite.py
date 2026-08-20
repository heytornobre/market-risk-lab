"""Typed SQLite repository with explicit transactions and run-state semantics."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from market_risk_engine import __version__
from market_risk_engine.data.loaders import FixtureBundle
from market_risk_engine.domain.models import (
    CalculationRun,
    Currency,
    FxRate,
    Instrument,
    Position,
    Price,
    RunStatus,
)
from market_risk_engine.exceptions import RepositoryError
from market_risk_engine.risk.models import (
    FactorMetricRecord,
    RiskResultRecord,
    StressResultRecord,
)
from market_risk_engine.storage.migrations import MIGRATIONS


def utc_timestamp(moment: datetime | None = None) -> str:
    value = moment or datetime.now(UTC)
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class SQLiteRepository:
    """SQLite-only repository; construction has no filesystem side effects."""

    def __init__(
        self,
        database_path: Path,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.database_path = database_path
        self._clock = clock or (lambda: datetime.now(UTC))

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def migrate(self) -> list[int]:
        applied_now: list[int] = []
        with self.transaction() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
            applied = {
                row["version"]
                for row in connection.execute("SELECT version FROM schema_migrations")
            }
            for version in sorted(MIGRATIONS):
                if version in applied:
                    continue
                for statement in MIGRATIONS[version]:
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (version, utc_timestamp(self._clock())),
                )
                applied_now.append(version)
        return applied_now

    def schema_versions(self) -> list[int]:
        with self.connect() as connection:
            try:
                rows = connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                ).fetchall()
            except sqlite3.OperationalError as error:
                raise RepositoryError("database is not migrated; run 'db migrate'") from error
        return [int(row["version"]) for row in rows]

    def load_fixture(self, bundle: FixtureBundle) -> None:
        spec = bundle.spec
        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT specification_hash FROM fixture_loads WHERE specification_version = ?",
                (spec.specification_version,),
            ).fetchone()
            if existing is not None and existing["specification_hash"] != bundle.specification_hash:
                raise RepositoryError(
                    "fixture version already exists with different contents; increment the version"
                )
            connection.execute(
                """
                INSERT OR IGNORE INTO fixture_loads(
                    specification_version, specification_hash, random_seed, base_currency,
                    start_date, end_date, loaded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    spec.specification_version,
                    bundle.specification_hash,
                    spec.seed,
                    spec.base_currency.value,
                    spec.start_date.isoformat(),
                    spec.end_date.isoformat(),
                    utc_timestamp(self._clock()),
                ),
            )
            connection.executemany(
                """
                INSERT INTO instruments(
                    instrument_id, display_name, asset_class, quote_currency,
                    price_multiplier, factor_classification
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(instrument_id) DO UPDATE SET
                    display_name=excluded.display_name,
                    asset_class=excluded.asset_class,
                    quote_currency=excluded.quote_currency,
                    price_multiplier=excluded.price_multiplier,
                    factor_classification=excluded.factor_classification
                """,
                [
                    (
                        item.instrument_id,
                        item.display_name,
                        item.asset_class.value,
                        item.quote_currency.value,
                        item.price_multiplier,
                        item.factor_classification,
                    )
                    for item in bundle.instruments
                ],
            )
            connection.executemany(
                """
                INSERT INTO positions(
                    portfolio_id, effective_date, instrument_id, quantity, unit_cost
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(portfolio_id, effective_date, instrument_id) DO UPDATE SET
                    quantity=excluded.quantity, unit_cost=excluded.unit_cost
                """,
                [
                    (
                        item.portfolio_id,
                        item.effective_date.isoformat(),
                        item.instrument_id,
                        item.quantity,
                        item.unit_cost,
                    )
                    for item in bundle.positions
                ],
            )
            connection.executemany(
                """
                INSERT INTO prices(date, instrument_id, close) VALUES (?, ?, ?)
                ON CONFLICT(date, instrument_id) DO UPDATE SET close=excluded.close
                """,
                [(item.date.isoformat(), item.instrument_id, item.close) for item in bundle.prices],
            )
            connection.executemany(
                """
                INSERT INTO fx_rates(date, base_currency, quote_currency, rate)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(date, base_currency, quote_currency) DO UPDATE SET rate=excluded.rate
                """,
                [
                    (
                        item.date.isoformat(),
                        item.base_currency.value,
                        item.quote_currency.value,
                        item.rate,
                    )
                    for item in bundle.fx_rates
                ],
            )

    def instruments(self) -> list[Instrument]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM instruments ORDER BY instrument_id").fetchall()
        return [Instrument.model_validate(dict(row)) for row in rows]

    def positions(self) -> list[Position]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM positions
                ORDER BY portfolio_id, effective_date, instrument_id
                """
            ).fetchall()
        return [Position.model_validate(dict(row)) for row in rows]

    def prices(self) -> list[Price]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM prices ORDER BY date, instrument_id"
            ).fetchall()
        return [Price.model_validate(dict(row)) for row in rows]

    def fx_rates(self) -> list[FxRate]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM fx_rates ORDER BY date, base_currency, quote_currency"
            ).fetchall()
        return [FxRate.model_validate(dict(row)) for row in rows]

    def create_calculation_run(
        self,
        *,
        run_id: str,
        portfolio_id: str,
        fixture_version: str,
        random_seed: int,
        base_currency: Currency,
        input_data_cutoff: date,
        effective_position_date: date,
    ) -> CalculationRun:
        requested_at = utc_timestamp(self._clock())
        with self.transaction() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO calculation_runs(
                        run_id, status, fixture_version, random_seed, base_currency,
                        input_data_cutoff, effective_position_date, package_version,
                        requested_at, completed_at, failure_reason, portfolio_id
                    ) VALUES (?, 'pending', ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?)
                    """,
                    (
                        run_id,
                        fixture_version,
                        random_seed,
                        base_currency.value,
                        input_data_cutoff.isoformat(),
                        effective_position_date.isoformat(),
                        __version__,
                        requested_at,
                        portfolio_id,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise RepositoryError(
                    f"unable to create calculation run {run_id}: {error}"
                ) from error
        return self.get_calculation_run(run_id)

    def transition_run(
        self, run_id: str, target: RunStatus, *, failure_reason: str | None = None
    ) -> CalculationRun:
        allowed = {
            RunStatus.PENDING: {RunStatus.RUNNING, RunStatus.FAILED},
            RunStatus.RUNNING: {RunStatus.SUCCEEDED, RunStatus.FAILED},
            RunStatus.SUCCEEDED: set(),
            RunStatus.FAILED: set(),
        }
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT status FROM calculation_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise RepositoryError(f"unknown calculation run: {run_id}")
            current = RunStatus(row["status"])
            if target not in allowed[current]:
                raise RepositoryError(f"invalid run transition: {current.value} -> {target.value}")
            if target is RunStatus.FAILED:
                reason = (failure_reason or "").strip()
                if not reason:
                    raise RepositoryError("failed runs require a failure reason")
                completed_at = utc_timestamp(self._clock())
            elif target is RunStatus.SUCCEEDED:
                reason = None
                completed_at = utc_timestamp(self._clock())
            else:
                reason = None
                completed_at = None
            connection.execute(
                """
                UPDATE calculation_runs
                SET status = ?, completed_at = ?, failure_reason = ?
                WHERE run_id = ?
                """,
                (target.value, completed_at, reason, run_id),
            )
        return self.get_calculation_run(run_id)

    def get_calculation_run(self, run_id: str) -> CalculationRun:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM calculation_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise RepositoryError(f"unknown calculation run: {run_id}")
        payload: dict[str, Any] = dict(row)
        payload["requested_at"] = _parse_timestamp(payload["requested_at"])
        if payload["completed_at"] is not None:
            payload["completed_at"] = _parse_timestamp(payload["completed_at"])
        return CalculationRun.model_validate(payload)

    def commit_calculation_results(
        self,
        run_id: str,
        *,
        effective_position_date: date,
        risk_results: tuple[RiskResultRecord, ...],
        stress_results: tuple[StressResultRecord, ...],
        factor_metrics: tuple[FactorMetricRecord, ...],
    ) -> None:
        created_at = utc_timestamp(self._clock())
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT status FROM calculation_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise RepositoryError(f"unknown calculation run: {run_id}")
            if row["status"] != RunStatus.RUNNING.value:
                raise RepositoryError("results can only be committed for a running calculation")
            connection.executemany(
                """
                INSERT INTO risk_results(
                    run_id, method, model_variant, confidence_level, horizon, value,
                    parameter_hash, model_parameters_json, calculation_version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        run_id,
                        item.method,
                        item.model_variant,
                        item.confidence_level,
                        item.horizon,
                        item.value,
                        item.parameter_hash,
                        item.model_parameters_json,
                        item.calculation_version,
                        created_at,
                    )
                    for item in risk_results
                ],
            )
            connection.executemany(
                """
                INSERT INTO stress_results(
                    run_id, scenario_id, scenario_version, portfolio_return, pnl_eur,
                    covered_market_value_eur, gross_market_value_eur, coverage_ratio,
                    uncovered_instruments_json, model_parameters_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        run_id,
                        item.scenario_id,
                        item.scenario_version,
                        item.portfolio_return,
                        item.pnl_eur,
                        item.covered_market_value_eur,
                        item.gross_market_value_eur,
                        item.coverage_ratio,
                        json.dumps(item.uncovered_instruments, separators=(",", ":")),
                        item.model_parameters_json,
                        created_at,
                    )
                    for item in stress_results
                ],
            )
            connection.executemany(
                """
                INSERT INTO factor_metrics(
                    run_id, benchmark_instrument_id, metric, value, model_variant,
                    model_parameters_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        run_id,
                        item.benchmark_instrument_id,
                        item.metric,
                        item.value,
                        item.model_variant,
                        item.model_parameters_json,
                        created_at,
                    )
                    for item in factor_metrics
                ],
            )
            connection.execute(
                """
                UPDATE calculation_runs
                SET status = 'succeeded', completed_at = ?, failure_reason = NULL,
                    effective_position_date = ?
                WHERE run_id = ?
                """,
                (created_at, effective_position_date.isoformat(), run_id),
            )

    def inspect_calculation(self, run_id: str | None = None) -> dict[str, Any]:
        with self.connect() as connection:
            if run_id is None:
                run = connection.execute(
                    "SELECT * FROM calculation_runs ORDER BY requested_at DESC, run_id DESC LIMIT 1"
                ).fetchone()
            else:
                run = connection.execute(
                    "SELECT * FROM calculation_runs WHERE run_id = ?", (run_id,)
                ).fetchone()
            if run is None:
                raise RepositoryError("no matching calculation run exists")
            selected_run_id = str(run["run_id"])
            risk_rows = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT method, model_variant, confidence_level, horizon, value,
                           parameter_hash, model_parameters_json, calculation_version
                    FROM risk_results WHERE run_id = ?
                    ORDER BY method, model_variant, confidence_level, horizon, parameter_hash
                    """,
                    (selected_run_id,),
                )
            ]
            stress_rows = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT scenario_id, scenario_version, portfolio_return, pnl_eur,
                           covered_market_value_eur, gross_market_value_eur, coverage_ratio,
                           uncovered_instruments_json, model_parameters_json
                    FROM stress_results WHERE run_id = ?
                    ORDER BY scenario_id, scenario_version
                    """,
                    (selected_run_id,),
                )
            ]
            factor_rows = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT benchmark_instrument_id, metric, value, model_variant,
                           model_parameters_json
                    FROM factor_metrics WHERE run_id = ?
                    ORDER BY benchmark_instrument_id, metric, model_variant
                    """,
                    (selected_run_id,),
                )
            ]
        return {
            "run": dict(run),
            "risk_results": risk_rows,
            "stress_results": stress_rows,
            "factor_metrics": factor_rows,
        }

    def inspect_fixture(self) -> dict[str, Any]:
        with self.connect() as connection:
            metadata = connection.execute(
                "SELECT * FROM fixture_loads ORDER BY specification_version DESC LIMIT 1"
            ).fetchone()
            if metadata is None:
                raise RepositoryError("no fixture is loaded; run 'demo load'")
            counts = {
                table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for table in ("instruments", "positions", "prices", "fx_rates")
            }
            price_range = connection.execute(
                "SELECT MIN(date) AS start_date, MAX(date) AS end_date FROM prices"
            ).fetchone()
            portfolios = [
                row[0]
                for row in connection.execute(
                    "SELECT DISTINCT portfolio_id FROM positions ORDER BY portfolio_id"
                )
            ]
            currencies = [
                row[0]
                for row in connection.execute(
                    "SELECT DISTINCT quote_currency FROM instruments ORDER BY quote_currency"
                )
            ]
        return {
            "fixture_version": metadata["specification_version"],
            "fixture_hash": metadata["specification_hash"],
            "seed": metadata["random_seed"],
            "base_currency": metadata["base_currency"],
            "date_range": [price_range["start_date"], price_range["end_date"]],
            "portfolios": portfolios,
            "currencies": currencies,
            "counts": counts,
        }
