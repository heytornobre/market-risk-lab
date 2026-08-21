"""Atomic A4 calculation-run orchestration."""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable
from pathlib import Path

import numpy as np

from market_risk_engine import __version__
from market_risk_engine.domain.models import Currency, RunStatus
from market_risk_engine.exceptions import MarketRiskLabError
from market_risk_engine.risk.factors import TRADING_DAYS, factor_metrics
from market_risk_engine.risk.historical import (
    QUANTILE_METHOD,
    empirical_var_cvar,
    overlapping_compounded_returns,
)
from market_risk_engine.risk.models import (
    CalculationOutput,
    CalculationRequest,
    FactorMetricRecord,
    RiskResultRecord,
    canonical_parameters,
)
from market_risk_engine.risk.monte_carlo import (
    COVARIANCE_TOLERANCE,
    monte_carlo_var_cvar,
)
from market_risk_engine.risk.parametric import normal_var
from market_risk_engine.risk.portfolio import prepare_portfolio_data
from market_risk_engine.risk.stress import load_scenario_set, run_scenarios
from market_risk_engine.storage.sqlite import SQLiteRepository

_LOCAL_PATH = re.compile(
    r"(?:"
    r"/(?:Users|home)/[^/\s:'\"]+(?:/[^\s:'\"]*)?"
    r"|/(?:private/)?tmp(?:/[^\s:'\"]*)?"
    r"|/private/var/folders(?:/[^\s:'\"]*)?"
    r"|[A-Za-z]:\\(?:Users\\[^\\\s:'\"]+|Windows\\Temp|Temp)(?:\\[^\s:'\"]*)?"
    r")"
)


def _redact_local_path(match: re.Match[str]) -> str:
    basename = re.split(r"[/\\]", match.group(0))[-1]
    return f"<local-path>/{basename}" if basename else "<local-path>"


def _risk_record(
    *,
    method: str,
    model_variant: str,
    confidence_level: float,
    horizon: int,
    value: float,
    parameters: dict[str, object],
) -> RiskResultRecord:
    encoded, parameter_hash = canonical_parameters(parameters)
    return RiskResultRecord(
        method=method,
        model_variant=model_variant,
        confidence_level=confidence_level,
        horizon=horizon,
        value=value,
        parameter_hash=parameter_hash,
        model_parameters_json=encoded,
        calculation_version=__version__,
    )


def _safe_failure_reason(error: Exception) -> str:
    message = " ".join(str(error).split())
    message = _LOCAL_PATH.sub(_redact_local_path, message)
    if not message:
        message = "calculation failed without an explanatory message"
    return f"{type(error).__name__}: {message}"[:500]


class CalculationService:
    def __init__(
        self,
        repository: SQLiteRepository,
        *,
        run_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.repository = repository
        self._run_id_factory = run_id_factory or (lambda: str(uuid.uuid4()))

    def run(self, request: CalculationRequest) -> CalculationOutput:
        fixture = self.repository.inspect_fixture()
        run_id = self._run_id_factory()
        run = self.repository.create_calculation_run(
            run_id=run_id,
            portfolio_id=request.portfolio_id,
            fixture_version=str(fixture["fixture_version"]),
            random_seed=request.monte_carlo_seed,
            base_currency=Currency.EUR,
            input_data_cutoff=request.as_of_date,
            effective_position_date=request.as_of_date,
        )
        self.repository.transition_run(run.run_id, RunStatus.RUNNING)
        try:
            data = prepare_portfolio_data(
                self.repository,
                portfolio_id=request.portfolio_id,
                as_of_date=request.as_of_date,
                benchmark_instrument_id=request.benchmark_instrument_id,
            )
            risk_records: list[RiskResultRecord] = []
            portfolio_returns = data.portfolio_simple_returns.to_numpy(dtype=np.float64)
            instrument_returns = data.instrument_simple_returns.to_numpy(dtype=np.float64)
            log_returns = data.instrument_log_returns.to_numpy(dtype=np.float64)

            for horizon in request.horizons:
                historical_returns = overlapping_compounded_returns(portfolio_returns, horizon)
                for confidence in request.confidence_levels:
                    historical_var, historical_cvar = empirical_var_cvar(
                        historical_returns, confidence
                    )
                    historical_parameters: dict[str, object] = {
                        "return_model": "constant_weight_daily_simple",
                        "horizon_model": "overlapping_compounded_simple_returns",
                        "loss_convention": "loss=-portfolio_return",
                        "quantile_method": QUANTILE_METHOD,
                        "cvar_tail_method": "equal_probability_fractional_boundary",
                        "observations": len(historical_returns),
                    }
                    risk_records.extend(
                        (
                            _risk_record(
                                method="historical_var",
                                model_variant="constant_weight_empirical_linear",
                                confidence_level=confidence,
                                horizon=horizon,
                                value=historical_var,
                                parameters=historical_parameters,
                            ),
                            _risk_record(
                                method="historical_cvar",
                                model_variant="constant_weight_fractional_tail_mass",
                                confidence_level=confidence,
                                horizon=horizon,
                                value=historical_cvar,
                                parameters=historical_parameters,
                            ),
                        )
                    )
                    parametric_parameters: dict[str, object] = {
                        "distribution": "normal",
                        "return_model": "constant_weight_daily_simple",
                        "mean_included": True,
                        "mean_horizon": "horizon*sample_daily_mean",
                        "volatility_horizon": "sqrt(horizon)*sample_daily_volatility",
                        "covariance": "sample_ddof_1",
                        "loss_convention": "loss=-portfolio_return",
                        "observations": len(instrument_returns),
                    }
                    risk_records.append(
                        _risk_record(
                            method="parametric_var",
                            model_variant="normal_sample_covariance_with_mean",
                            confidence_level=confidence,
                            horizon=horizon,
                            value=normal_var(
                                instrument_returns,
                                data.weights,
                                confidence,
                                horizon,
                                include_mean=True,
                            ),
                            parameters=parametric_parameters,
                        )
                    )
                    monte_carlo_var, monte_carlo_cvar, repair = monte_carlo_var_cvar(
                        log_returns,
                        data.weights,
                        confidence,
                        horizon,
                        seed=request.monte_carlo_seed,
                        simulations=request.monte_carlo_simulations,
                    )
                    monte_carlo_parameters: dict[str, object] = {
                        "bit_generator": "PCG64",
                        "seed": request.monte_carlo_seed,
                        "simulations": request.monte_carlo_simulations,
                        "return_model": "multivariate_daily_log_normal",
                        "mean": "sample_daily_log_return",
                        "covariance": "sample_daily_log_return_ddof_1",
                        "horizon_model": "simulate_each_day_then_compound",
                        "covariance_repaired": repair.repaired,
                        "minimum_eigenvalue": repair.minimum_eigenvalue,
                        "repair_tolerance": repair.tolerance,
                        "configured_relative_tolerance": COVARIANCE_TOLERANCE,
                        "numpy_version": np.__version__,
                        "loss_convention": "loss=-portfolio_return",
                    }
                    risk_records.extend(
                        (
                            _risk_record(
                                method="monte_carlo_var",
                                model_variant="constant_weight_multivariate_log_normal",
                                confidence_level=confidence,
                                horizon=horizon,
                                value=monte_carlo_var,
                                parameters=monte_carlo_parameters,
                            ),
                            _risk_record(
                                method="monte_carlo_cvar",
                                model_variant="constant_weight_multivariate_log_normal",
                                confidence_level=confidence,
                                horizon=horizon,
                                value=monte_carlo_cvar,
                                parameters=monte_carlo_parameters,
                            ),
                        )
                    )

            scenario_set = load_scenario_set(Path(request.scenario_set_path))
            stress_records = run_scenarios(data, scenario_set)
            metric_values = factor_metrics(
                portfolio_returns,
                data.benchmark_simple_returns.to_numpy(dtype=np.float64),
                annual_risk_free_rate=request.annual_risk_free_rate,
            )
            daily_risk_free = (
                None
                if request.annual_risk_free_rate is None
                else (1.0 + request.annual_risk_free_rate) ** (1.0 / TRADING_DAYS) - 1.0
            )
            factor_parameter_json = json.dumps(
                {
                    "annual_risk_free_rate": request.annual_risk_free_rate,
                    "daily_risk_free_rate": daily_risk_free,
                    "annualisation_days": TRADING_DAYS,
                    "statistics": "sample_ddof_1",
                    "return_model": "constant_weight_daily_simple",
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            factor_records = tuple(
                FactorMetricRecord(
                    benchmark_instrument_id=request.benchmark_instrument_id,
                    metric=metric,
                    value=value,
                    model_variant="sample_constant_weight_eur",
                    model_parameters_json=factor_parameter_json,
                )
                for metric, value in sorted(metric_values.items())
            )
            self.repository.commit_calculation_results(
                run_id,
                effective_position_date=data.effective_position_date,
                risk_results=tuple(risk_records),
                stress_results=stress_records,
                factor_metrics=factor_records,
            )
            return CalculationOutput(
                run_id=run_id,
                status="succeeded",
                effective_position_date=data.effective_position_date,
                portfolio_market_value_eur=data.gross_market_value_eur,
                risk_results=tuple(risk_records),
                stress_results=stress_records,
                factor_metrics=factor_records,
            )
        except Exception as error:
            safe_reason = _safe_failure_reason(error)
            try:
                self.repository.transition_run(run_id, RunStatus.FAILED, failure_reason=safe_reason)
            except Exception as status_error:
                raise MarketRiskLabError(
                    f"calculation failed and run status could not be recorded: {status_error}"
                ) from error
            raise MarketRiskLabError(safe_reason) from error
