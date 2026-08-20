from datetime import date
from pathlib import Path

import pytest

from market_risk_engine.exceptions import DataValidationError
from market_risk_engine.risk.portfolio import prepare_portfolio_data
from market_risk_engine.storage.sqlite import SQLiteRepository


def _small_repository(path: Path) -> SQLiteRepository:
    repository = SQLiteRepository(path)
    repository.migrate()
    with repository.transaction() as connection:
        connection.executemany(
            "INSERT INTO instruments VALUES (?,?,?,?,?,?)",
            [
                ("LAB_USD", "Synthetic USD", "equity", "USD", 2.0, None),
                ("LAB_BENCH", "Synthetic Benchmark", "equity", "EUR", 1.0, None),
            ],
        )
        connection.executemany(
            "INSERT INTO positions VALUES (?,?,?,?,?)",
            [
                ("LAB_PORT", "2025-01-01", "LAB_USD", 2.0, None),
                ("LAB_PORT", "2025-01-02", "LAB_USD", 5.0, None),
            ],
        )
        connection.executemany(
            "INSERT INTO prices VALUES (?,?,?)",
            [
                ("2025-01-02", "LAB_USD", 10.0),
                ("2025-01-03", "LAB_USD", 10.0),
                ("2025-01-02", "LAB_BENCH", 100.0),
                ("2025-01-03", "LAB_BENCH", 101.0),
            ],
        )
        connection.executemany(
            "INSERT INTO fx_rates VALUES (?,?,?,?)",
            [
                ("2025-01-02", "EUR", "EUR", 1.0),
                ("2025-01-03", "EUR", "EUR", 1.0),
                ("2025-01-02", "EUR", "USD", 0.90),
                ("2025-01-03", "EUR", "USD", 0.99),
            ],
        )
    return repository


def test_base_currency_valuation_multiplier_fx_and_effective_snapshot(tmp_path: Path) -> None:
    data = prepare_portfolio_data(
        _small_repository(tmp_path / "risk.db"),
        portfolio_id="LAB_PORT",
        as_of_date=date(2025, 1, 3),
        benchmark_instrument_id="LAB_BENCH",
    )
    assert data.effective_position_date == date(2025, 1, 2)
    assert data.market_values_eur.tolist() == pytest.approx([5 * 10 * 2 * 0.99])
    assert data.weights.tolist() == pytest.approx([1.0])
    assert data.portfolio_simple_returns.iloc[0] == pytest.approx(0.10)
    assert data.benchmark_simple_returns.iloc[0] == pytest.approx(0.01)


def test_missing_as_of_price_is_actionable(tmp_path: Path) -> None:
    repository = _small_repository(tmp_path / "risk.db")
    with pytest.raises(Exception, match="missing as-of price"):
        prepare_portfolio_data(
            repository,
            portfolio_id="LAB_PORT",
            as_of_date=date(2025, 1, 4),
            benchmark_instrument_id="LAB_BENCH",
        )


def test_a4_rejects_short_position_snapshot(tmp_path: Path) -> None:
    repository = _small_repository(tmp_path / "risk.db")
    with repository.transaction() as connection:
        connection.execute(
            """
            UPDATE positions SET quantity = -5
            WHERE portfolio_id = 'LAB_PORT' AND effective_date = '2025-01-02'
            """
        )
    with pytest.raises(DataValidationError, match="rejects short positions"):
        prepare_portfolio_data(
            repository,
            portfolio_id="LAB_PORT",
            as_of_date=date(2025, 1, 3),
            benchmark_instrument_id="LAB_BENCH",
        )
