"""Ordered, explicit SQLite schema migrations."""

MIGRATIONS: dict[int, tuple[str, ...]] = {
    1: (
        """
        CREATE TABLE instruments (
            instrument_id TEXT PRIMARY KEY CHECK (length(trim(instrument_id)) > 0),
            display_name TEXT NOT NULL CHECK (length(trim(display_name)) > 0),
            asset_class TEXT NOT NULL CHECK (
                asset_class IN ('equity','government_bond','corporate_credit','commodity','cash')
            ),
            quote_currency TEXT NOT NULL CHECK (quote_currency IN ('EUR','USD','GBP')),
            price_multiplier REAL NOT NULL CHECK (price_multiplier > 0),
            factor_classification TEXT
        )
        """,
        """
        CREATE TABLE positions (
            portfolio_id TEXT NOT NULL CHECK (length(trim(portfolio_id)) > 0),
            effective_date TEXT NOT NULL,
            instrument_id TEXT NOT NULL REFERENCES instruments(instrument_id),
            quantity REAL NOT NULL CHECK (quantity != 0),
            unit_cost REAL CHECK (unit_cost IS NULL OR unit_cost > 0),
            PRIMARY KEY (portfolio_id, effective_date, instrument_id)
        )
        """,
        """
        CREATE TABLE prices (
            date TEXT NOT NULL,
            instrument_id TEXT NOT NULL REFERENCES instruments(instrument_id),
            close REAL NOT NULL CHECK (close > 0),
            PRIMARY KEY (date, instrument_id)
        )
        """,
        """
        CREATE TABLE fx_rates (
            date TEXT NOT NULL,
            base_currency TEXT NOT NULL CHECK (base_currency IN ('EUR','USD','GBP')),
            quote_currency TEXT NOT NULL CHECK (quote_currency IN ('EUR','USD','GBP')),
            rate REAL NOT NULL CHECK (rate > 0),
            PRIMARY KEY (date, base_currency, quote_currency)
        )
        """,
        """
        CREATE TABLE fixture_loads (
            specification_version TEXT PRIMARY KEY,
            specification_hash TEXT NOT NULL UNIQUE,
            random_seed INTEGER NOT NULL CHECK (random_seed >= 0),
            base_currency TEXT NOT NULL CHECK (base_currency IN ('EUR','USD','GBP')),
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            loaded_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE calculation_runs (
            run_id TEXT PRIMARY KEY CHECK (length(trim(run_id)) > 0),
            status TEXT NOT NULL CHECK (status IN ('pending','running','succeeded','failed')),
            fixture_version TEXT NOT NULL,
            random_seed INTEGER NOT NULL CHECK (random_seed >= 0),
            base_currency TEXT NOT NULL CHECK (base_currency IN ('EUR','USD','GBP')),
            input_data_cutoff TEXT NOT NULL,
            effective_position_date TEXT NOT NULL,
            package_version TEXT NOT NULL,
            requested_at TEXT NOT NULL,
            completed_at TEXT,
            failure_reason TEXT,
            FOREIGN KEY (fixture_version) REFERENCES fixture_loads(specification_version),
            CHECK (
                (status IN ('pending','running')
                    AND completed_at IS NULL AND failure_reason IS NULL)
                OR (status = 'succeeded' AND completed_at IS NOT NULL AND failure_reason IS NULL)
                OR (status = 'failed' AND completed_at IS NOT NULL
                    AND length(trim(failure_reason)) > 0)
            )
        )
        """,
        """
        CREATE TABLE risk_results (
            run_id TEXT NOT NULL REFERENCES calculation_runs(run_id),
            method TEXT NOT NULL CHECK (length(trim(method)) > 0),
            model_variant TEXT NOT NULL CHECK (length(trim(model_variant)) > 0),
            confidence_level REAL NOT NULL CHECK (confidence_level > 0 AND confidence_level < 1),
            horizon INTEGER NOT NULL CHECK (horizon > 0),
            value REAL NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (run_id, method, model_variant, confidence_level, horizon)
        )
        """,
        "CREATE INDEX positions_effective_date_idx ON positions(effective_date)",
        "CREATE INDEX prices_instrument_date_idx ON prices(instrument_id, date)",
        "CREATE INDEX fx_rates_currency_date_idx ON fx_rates(base_currency, quote_currency, date)",
    ),
    2: (
        """
        ALTER TABLE calculation_runs
        ADD COLUMN portfolio_id TEXT NOT NULL DEFAULT 'migration-1-unknown'
        """,
        "ALTER TABLE risk_results RENAME TO risk_results_migration_1",
        """
        CREATE TABLE risk_results (
            run_id TEXT NOT NULL REFERENCES calculation_runs(run_id),
            method TEXT NOT NULL CHECK (length(trim(method)) > 0),
            model_variant TEXT NOT NULL CHECK (length(trim(model_variant)) > 0),
            confidence_level REAL NOT NULL CHECK (confidence_level > 0 AND confidence_level < 1),
            horizon INTEGER NOT NULL CHECK (horizon > 0),
            value REAL NOT NULL,
            parameter_hash TEXT NOT NULL CHECK (length(parameter_hash) = 64),
            model_parameters_json TEXT NOT NULL,
            calculation_version TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (
                run_id, method, model_variant, confidence_level, horizon, parameter_hash
            )
        )
        """,
        """
        INSERT INTO risk_results(
            run_id, method, model_variant, confidence_level, horizon, value,
            parameter_hash, model_parameters_json, calculation_version, created_at
        )
        SELECT run_id, method, model_variant, confidence_level, horizon, value,
               '0000000000000000000000000000000000000000000000000000000000000000',
               '{}', 'migration-1', created_at
        FROM risk_results_migration_1
        """,
        "DROP TABLE risk_results_migration_1",
        """
        CREATE TABLE stress_results (
            run_id TEXT NOT NULL REFERENCES calculation_runs(run_id),
            scenario_id TEXT NOT NULL CHECK (length(trim(scenario_id)) > 0),
            scenario_version TEXT NOT NULL CHECK (length(trim(scenario_version)) > 0),
            portfolio_return REAL NOT NULL,
            pnl_eur REAL NOT NULL,
            covered_market_value_eur REAL NOT NULL CHECK (covered_market_value_eur > 0),
            gross_market_value_eur REAL NOT NULL CHECK (gross_market_value_eur > 0),
            coverage_ratio REAL NOT NULL CHECK (coverage_ratio > 0 AND coverage_ratio <= 1),
            uncovered_instruments_json TEXT NOT NULL,
            model_parameters_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (run_id, scenario_id, scenario_version)
        )
        """,
        """
        CREATE TABLE factor_metrics (
            run_id TEXT NOT NULL REFERENCES calculation_runs(run_id),
            benchmark_instrument_id TEXT NOT NULL REFERENCES instruments(instrument_id),
            metric TEXT NOT NULL CHECK (
                metric IN ('beta','correlation','annualised_volatility',
                           'tracking_error','annualised_excess_return_alpha')
            ),
            value REAL NOT NULL,
            model_variant TEXT NOT NULL,
            model_parameters_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (run_id, benchmark_instrument_id, metric, model_variant)
        )
        """,
        "CREATE INDEX risk_results_run_idx ON risk_results(run_id)",
        "CREATE INDEX stress_results_run_idx ON stress_results(run_id)",
        "CREATE INDEX factor_metrics_run_idx ON factor_metrics(run_id)",
    ),
}
