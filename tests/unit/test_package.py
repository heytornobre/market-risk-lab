from typer.testing import CliRunner

from market_risk_engine import __version__
from market_risk_engine.cli import app


def test_package_version() -> None:
    assert __version__ == "0.1.0"


def test_status_command() -> None:
    result = CliRunner().invoke(app, ["status"])

    assert result.exit_code == 0
    assert "local public-release candidate" in result.stdout
