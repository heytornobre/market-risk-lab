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

This construction has a unit diagonal and is positive definite for the supplied
independent residual variances when every
row's squared loadings sum to less than one. The generator verifies that
condition, exact symmetry, positive Cholesky pivots, deterministic series ordering,
and reconstruction of the intended correlation matrix with relative and absolute
tolerance `1e-12`.

The generator sorts factors, instruments, FX series, dates, and output rows. It
uses generator algorithm `fixed-order-cholesky-v1`. Correlations, Cholesky entries,
correlated shocks, and geometric paths use explicitly ordered scalar operations;
this avoids BLAS/LAPACK-dependent eigenvector orientations and reduction order.
The independent normal stream comes from NumPy's seeded default PCG64 generator.
CSV output uses ten decimal places, UTF-8, LF newlines, fixed columns, and sorted
rows. The algorithm identifier is part of the authoritative TOML specification;
the specification hash and version are persisted when the fixture is loaded.
Under the locked dependency environment, the same specification and seed generate
byte-identical files on macOS and Linux.

Specification `1.1.0` intentionally replaces the earlier eigendecomposition-based
realisation. Both approaches represent the configured covariance structure, but
eigenvector orientation is not portable across numerical-library backends. The
fixed-order Cholesky mapping gives the seed an unambiguous cross-platform meaning.

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
