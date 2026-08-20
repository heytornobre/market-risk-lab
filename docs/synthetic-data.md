# Deterministic synthetic-data specification

All identities, quantities, prices, FX rates, and portfolio choices in this
project are fictional. They do not model, anonymise, perturb, or approximate a
real portfolio.

The versioned TOML specification fixes a seed, a business-date interval, five
named latent factors, 13 instruments, two stochastic non-EUR FX series, and one
effective-dated position snapshot. Instruments cover equities, government bonds,
corporate credit, commodities, and cash across EUR, USD, and GBP.

## Path construction

For each local price and non-EUR FX series, the generator uses geometric paths
with explicit annual drift and volatility. Named factor loadings create shared
shocks. Remaining variance is independent and series-specific. The correlation
matrix is constructed as:

`L × Lᵀ + diagonal(1 − row_sum(L²))`

This construction has a unit diagonal and is positive semidefinite when every
row's squared loadings sum to less than one. The generator verifies that
condition and checks the resulting eigenvalues.

The generator sorts factors, instruments, FX series, dates, and output rows. It
uses NumPy's seeded generator, fixed decimal formatting, UTF-8, and LF newlines.
Under the locked dependency environment, repeated generation is byte-for-byte
stable.

## FX convention

- Portfolio base currency: EUR.
- `rate` means EUR per one unit of `quote_currency`.
- EUR/EUR is exactly `1.0` on every required business date.
- EUR price equals local closing price multiplied by the same-date FX rate.

## Limitations

The paths are designed to test engineering behavior, not to forecast markets.
Geometric returns, constant parameters, and a small latent-factor structure omit
regime changes, jumps, liquidity effects, transaction costs, and issuer-specific
fundamentals. A business-day calendar is used without exchange-specific holidays.
