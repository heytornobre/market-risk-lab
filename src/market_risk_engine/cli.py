"""Command-line interface for deterministic fixtures and SQLite persistence."""

import json
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Annotated

import typer

from market_risk_engine.dashboard.data import validate_existing_database
from market_risk_engine.data.loaders import load_fixture_bundle, load_specification
from market_risk_engine.data.synthetic import generate_fixtures
from market_risk_engine.exceptions import MarketRiskLabError
from market_risk_engine.risk.models import CalculationRequest
from market_risk_engine.risk.service import CalculationService
from market_risk_engine.storage.sqlite import SQLiteRepository

app = typer.Typer(add_completion=False, help="Market Risk Lab synthetic-data toolkit.")
demo_app = typer.Typer(help="Generate, load, and inspect deterministic demo data.")
database_app = typer.Typer(help="Manage the local SQLite database.")
risk_app = typer.Typer(help="Execute and inspect deterministic risk calculations.")
app.add_typer(demo_app, name="demo")
app.add_typer(database_app, name="db")
app.add_typer(risk_app, name="risk")

DEFAULT_DATA_DIRECTORY = Path("data/synthetic")
DEFAULT_SPECIFICATION = DEFAULT_DATA_DIRECTORY / "fixture-spec.toml"
DEFAULT_DATABASE = Path("var/market-risk-lab.db")
DEFAULT_SCENARIOS = DEFAULT_DATA_DIRECTORY / "stress-scenarios.toml"


def _fail(error: Exception) -> None:
    typer.echo(f"Error: {error}", err=True)
    raise typer.Exit(code=1) from error


@app.callback()
def main() -> None:
    """Market Risk Lab command group."""


@app.command()
def status() -> None:
    """Show the current implementation status."""
    typer.echo("market-risk-lab: local public-release candidate (synthetic SQLite MVP)")


@app.command()
def dashboard(
    database: Annotated[Path, typer.Option("--database", "-d")] = DEFAULT_DATABASE,
) -> None:
    """Start the local read-only Streamlit dashboard."""
    try:
        validate_existing_database(database)
        try:
            import streamlit  # noqa: F401
        except ImportError as error:
            raise MarketRiskLabError(
                "Streamlit is not installed; run 'uv sync --frozen --extra dashboard'"
            ) from error
        command = [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(Path(__file__).parent / "dashboard" / "app.py"),
            "--server.headless=true",
            "--server.address=127.0.0.1",
            "--browser.gatherUsageStats=false",
            "--",
            "--database",
            str(database.resolve()),
        ]
        completed = subprocess.run(command, check=False)
        if completed.returncode != 0:
            raise MarketRiskLabError(f"dashboard process exited with status {completed.returncode}")
    except (MarketRiskLabError, OSError) as error:
        _fail(error)


@demo_app.command("generate")
def generate_demo(
    specification: Annotated[Path, typer.Option("--specification", "-s")] = DEFAULT_SPECIFICATION,
    output_directory: Annotated[Path, typer.Option("--output-dir", "-o")] = DEFAULT_DATA_DIRECTORY,
) -> None:
    """Generate byte-stable synthetic CSV fixtures from the TOML specification."""
    try:
        spec = load_specification(specification)
        output_directory.mkdir(parents=True, exist_ok=True)
        target_specification = output_directory / "fixture-spec.toml"
        if specification.resolve() != target_specification.resolve():
            shutil.copyfile(specification, target_specification)
        hashes = generate_fixtures(spec, output_directory)
        typer.echo(
            json.dumps(
                {"specification_version": spec.specification_version, "files": hashes},
                sort_keys=True,
            )
        )
    except (MarketRiskLabError, OSError, ValueError) as error:
        _fail(error)


@database_app.command("migrate")
def migrate_database(
    database: Annotated[Path, typer.Option("--database", "-d")] = DEFAULT_DATABASE,
) -> None:
    """Create or idempotently migrate the local SQLite schema."""
    try:
        database.parent.mkdir(parents=True, exist_ok=True)
        repository = SQLiteRepository(database)
        applied = repository.migrate()
        typer.echo(json.dumps({"database": str(database), "applied_migrations": applied}))
    except (MarketRiskLabError, OSError) as error:
        _fail(error)


@demo_app.command("load")
def load_demo(
    data_directory: Annotated[Path, typer.Option("--data-dir")] = DEFAULT_DATA_DIRECTORY,
    database: Annotated[Path, typer.Option("--database", "-d")] = DEFAULT_DATABASE,
) -> None:
    """Validate and idempotently load the complete synthetic fixture."""
    try:
        bundle = load_fixture_bundle(data_directory)
        database.parent.mkdir(parents=True, exist_ok=True)
        repository = SQLiteRepository(database)
        repository.migrate()
        repository.load_fixture(bundle)
        summary = repository.inspect_fixture()
        typer.echo(json.dumps(summary, sort_keys=True))
    except (MarketRiskLabError, OSError) as error:
        _fail(error)


@demo_app.command("inspect")
def inspect_demo(
    database: Annotated[Path, typer.Option("--database", "-d")] = DEFAULT_DATABASE,
) -> None:
    """Inspect loaded portfolio counts, coverage, and fixture provenance."""
    try:
        validate_existing_database(database)
        summary = SQLiteRepository(database).inspect_fixture()
        typer.echo(json.dumps(summary, sort_keys=True))
    except (MarketRiskLabError, OSError) as error:
        _fail(error)


def _parse_float_list(value: str) -> tuple[float, ...]:
    try:
        return tuple(float(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as error:
        raise ValueError("confidence levels must be comma-separated numbers") from error


def _parse_int_list(value: str) -> tuple[int, ...]:
    try:
        return tuple(int(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as error:
        raise ValueError("horizons must be comma-separated integers") from error


@risk_app.command("run")
def run_risk(
    database: Annotated[Path, typer.Option("--database", "-d")] = DEFAULT_DATABASE,
    portfolio: Annotated[str, typer.Option("--portfolio")] = "SYNTHETIC_LAB_PORTFOLIO",
    as_of: Annotated[str, typer.Option("--as-of")] = "2025-12-31",
    confidence_levels: Annotated[str, typer.Option("--confidence-levels")] = "0.95,0.99",
    horizons: Annotated[str, typer.Option("--horizons")] = "1,10",
    monte_carlo_seed: Annotated[int, typer.Option("--mc-seed")] = 20260815,
    monte_carlo_simulations: Annotated[int, typer.Option("--mc-simulations")] = 20_000,
    benchmark: Annotated[str, typer.Option("--benchmark")] = "LAB_EQ_EUR_A",
    scenario_set: Annotated[Path, typer.Option("--scenario-set")] = DEFAULT_SCENARIOS,
    annual_risk_free_rate: Annotated[float | None, typer.Option("--annual-risk-free-rate")] = None,
) -> None:
    """Run the complete A4 calculation set and atomically persist its results."""
    try:
        request = CalculationRequest(
            portfolio_id=portfolio,
            as_of_date=date.fromisoformat(as_of),
            confidence_levels=_parse_float_list(confidence_levels),
            horizons=_parse_int_list(horizons),
            monte_carlo_seed=monte_carlo_seed,
            monte_carlo_simulations=monte_carlo_simulations,
            benchmark_instrument_id=benchmark,
            scenario_set_path=str(scenario_set),
            annual_risk_free_rate=annual_risk_free_rate,
        )
        repository = SQLiteRepository(database)
        repository.migrate()
        output = CalculationService(repository).run(request)
        typer.echo(
            json.dumps(
                {
                    "run_id": output.run_id,
                    "status": output.status,
                    "effective_position_date": output.effective_position_date.isoformat(),
                    "portfolio_market_value_eur": output.portfolio_market_value_eur,
                    "methods": sorted({item.method for item in output.risk_results}),
                    "risk_result_count": len(output.risk_results),
                    "stress_result_count": len(output.stress_results),
                    "factor_metric_count": len(output.factor_metrics),
                },
                sort_keys=True,
            )
        )
    except (MarketRiskLabError, OSError, ValueError) as error:
        _fail(error)


@risk_app.command("inspect")
def inspect_risk(
    database: Annotated[Path, typer.Option("--database", "-d")] = DEFAULT_DATABASE,
    run_id: Annotated[str | None, typer.Option("--run-id")] = None,
) -> None:
    """Inspect a selected run, or the most recently requested run."""
    try:
        validate_existing_database(database)
        typer.echo(
            json.dumps(SQLiteRepository(database).inspect_calculation(run_id), sort_keys=True)
        )
    except (MarketRiskLabError, OSError) as error:
        _fail(error)
