import builtins
import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from market_risk_engine.cli import app

runner = CliRunner()


def test_complete_generate_migrate_load_inspect_flow(
    fixture_directory: Path, tmp_path: Path
) -> None:
    generated = tmp_path / "synthetic"
    database = tmp_path / "state" / "risk.db"
    result = runner.invoke(
        app,
        [
            "demo",
            "generate",
            "--specification",
            str(fixture_directory / "fixture-spec.toml"),
            "--output-dir",
            str(generated),
        ],
    )
    assert result.exit_code == 0, result.output
    assert (generated / "fixture-spec.toml").exists()
    generation = json.loads(result.stdout)
    assert generation["specification_version"] == "1.1.0"
    assert generation["generator_version"] == "fixed-order-cholesky-v1"

    result = runner.invoke(app, ["db", "migrate", "--database", str(database)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["applied_migrations"] == [1, 2]

    for _ in range(2):
        result = runner.invoke(
            app,
            ["demo", "load", "--data-dir", str(generated), "--database", str(database)],
        )
        assert result.exit_code == 0, result.output

    missing_scenario = tmp_path / "missing-scenarios.toml"
    result = runner.invoke(
        app,
        [
            "risk",
            "run",
            "--database",
            str(database),
            "--scenario-set",
            str(missing_scenario),
            "--confidence-levels",
            "0.95",
            "--horizons",
            "1",
            "--mc-simulations",
            "1000",
        ],
    )
    assert result.exit_code == 1
    assert str(tmp_path) not in result.stderr
    assert "invalid stress scenario set <local-path>/missing-scenarios.toml" in result.stderr

    result = runner.invoke(app, ["demo", "inspect", "--database", str(database)])
    assert result.exit_code == 0, result.output
    summary = json.loads(result.stdout)
    assert summary["fixture_version"] == "1.1.0"
    assert summary["counts"]["instruments"] == 13
    assert summary["counts"]["positions"] == 13
    assert summary["currencies"] == ["EUR", "GBP", "USD"]

    result = runner.invoke(
        app,
        [
            "risk",
            "run",
            "--database",
            str(database),
            "--confidence-levels",
            "0.95",
            "--horizons",
            "1",
            "--mc-simulations",
            "1000",
        ],
    )
    assert result.exit_code == 0, result.output
    risk_summary = json.loads(result.stdout)
    assert risk_summary["status"] == "succeeded"
    assert risk_summary["risk_result_count"] == 5
    assert risk_summary["stress_result_count"] == 3
    result = runner.invoke(
        app,
        ["risk", "inspect", "--database", str(database), "--run-id", risk_summary["run_id"]],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["run"]["status"] == "succeeded"


def test_cli_reports_actionable_failure(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "demo",
            "load",
            "--data-dir",
            str(tmp_path / "missing"),
            "--database",
            str(tmp_path / "risk.db"),
        ],
    )
    assert result.exit_code == 1
    assert "Error: invalid fixture specification" in result.stderr


def test_inspect_commands_reject_missing_database_without_creating_paths(tmp_path: Path) -> None:
    database = tmp_path / "missing" / "risk.db"
    for command in ("demo", "risk"):
        result = runner.invoke(app, [command, "inspect", "--database", str(database)])
        assert result.exit_code == 1
        assert "Error: database not found:" in result.stderr
        assert "run 'db migrate' and 'demo load' first" in result.stderr
        assert not database.parent.exists()


def test_dashboard_command_passes_database_without_shell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "risk.db"
    database.touch()
    captured: list[str] = []

    def fake_run(command: list[str], *, check: bool) -> subprocess.CompletedProcess[str]:
        assert check is False
        captured.extend(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = runner.invoke(app, ["dashboard", "--database", str(database)])

    assert result.exit_code == 0, result.output
    assert "-m" in captured
    assert "streamlit" in captured
    assert "--server.address=127.0.0.1" in captured
    assert "--browser.gatherUsageStats=false" in captured
    assert "--database" in captured
    assert str(database.resolve()) in captured


def test_dashboard_command_reports_missing_streamlit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "risk.db"
    database.touch()
    original_import = builtins.__import__

    def import_without_streamlit(
        name: str,
        globals: dict[str, object] | None = None,
        locals: dict[str, object] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        if name == "streamlit":
            raise ImportError("simulated missing optional dependency")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", import_without_streamlit)
    result = runner.invoke(app, ["dashboard", "--database", str(database)])

    assert result.exit_code == 1
    assert "Streamlit is not installed" in result.stderr
    assert "uv sync --frozen --extra dashboard" in result.stderr
