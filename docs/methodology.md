# Market-risk methodology

Market Risk Lab is an engineering demonstration built exclusively from
deterministic synthetic data. It is not a realised-performance backtest,
production risk infrastructure, regulatory reporting, investment advice, or a
claim that these models are suitable for any particular portfolio.

## EUR valuation and weights

For instrument `i` and date `t`:

```text
EUR price(i,t) = local close(i,t)
                 × price multiplier(i)
                 × EUR-per-quote-currency FX(i,t)

market value(i,t) = quantity(i) × EUR price(i,t)
weight(i,t) = market value(i,t) / gross portfolio market value(t)
```

The effective position snapshot is the latest snapshot on or before the
requested as-of date. The MVP is long-only and rejects negative quantities rather
than introducing ambiguous gross-versus-net weighting. Missing same-date prices
or FX rates fail the run.

## Return and loss conventions

Converted prices use daily simple returns. A fixed vector of as-of weights is
applied to the complete historical instrument-return matrix. This creates a
constant-weight hypothetical risk-model series; it is not realised portfolio
performance or a historical backtest.

```text
portfolio return(t) = sum(weight(i) × simple return(i,t))
loss(t) = -portfolio return(t)
```

Loss metrics are floored at zero if the selected tail is still a gain. The code
never applies `abs()` to a signed return or P&L.

## Historical VaR and CVaR

For horizon `h`, overlapping simple returns are compounded directly:

```text
R(t,h) = product(1 + R(t+j), j=0..h-1) - 1
```

Historical VaR is the confidence-level quantile of `-R(t,h)` using NumPy's
explicit `method="linear"`. NumPy documents the available quantile estimators at
<https://numpy.org/doc/stable/reference/generated/numpy.quantile.html>.

CVaR uses an equal-probability empirical tail with a fractional boundary rank.
For `n` observations and confidence `c`, the upper loss-tail mass is `n×(1-c)`.
The worst `floor(n×(1-c))` observations receive full mass and the next ordered
observation receives the remaining fractional mass. This definition does not
depend on whether observations tied with interpolated VaR happen to satisfy a
`loss >= VaR` comparison.

Historical multi-day risk does not use square-root-of-time scaling.

## Parametric VaR

The variance-covariance model estimates instrument simple-return sample means
and sample covariance with `ddof=1`. For fixed weights `w`:

```text
mu_daily = wᵀ mean(instrument returns)
sigma_daily² = wᵀ covariance(instrument returns) w
mu_h = h × mu_daily
sigma_h = sqrt(h) × sigma_daily
VaR(c,h) = max(0, -mu_h + NormalPPF(c) × sigma_h)
```

The mean is included and persisted as model metadata. Normal quantiles use
SciPy's documented normal distribution implementation:
<https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.norm.html>.
Parametric CVaR is intentionally outside the MVP.

## Monte Carlo VaR and CVaR

The model estimates daily log-return sample means and covariance from complete
EUR-converted histories. It uses exactly
`numpy.random.Generator(numpy.random.PCG64(seed))`; NumPy documents PCG64 and its
fixed-seed integer-stream guarantee at
<https://numpy.org/doc/stable/reference/random/bit_generators/pcg64.html>.

Each simulation draws correlated daily log returns for every day of the complete
requested horizon. Instrument horizon log returns are summed, converted using
`expm1`, and combined with fixed starting weights. This construction cannot
generate an instrument simple return below -100%.

The sample covariance is symmetrised and checked by eigenvalue decomposition.
Negative eigenvalues within a relative `1e-10` numerical tolerance are clipped
to zero and the repair is persisted. A materially negative eigenvalue fails the
run. Seed, simulation count, horizon, confidence, mean/covariance conventions,
repair status, NumPy version, package version, and canonical parameters are
persisted. Reproducibility is scoped to the dependency lock and calculation
version.

## Stress scenarios

The public scenario TOML is original and synthetic. Local shocks may be mapped
by asset class or instrument; defining both for one instrument is rejected.
Currency shocks apply independently. When both local and currency shocks apply:

```text
combined return = (1 + local shock) × (1 + FX shock) - 1
```

Scenario P&L retains its sign. Each result records covered and gross market
value, coverage ratio, and uncovered instruments. Zero covered exposure is an
error, not a zero-loss result.

## Factor metrics

The benchmark must be an explicit loaded instrument and is converted into EUR
before returns are calculated. All dispersion and covariance statistics use
sample conventions (`ddof=1`):

```text
beta = sample_cov(portfolio, benchmark) / sample_var(benchmark)
correlation = sample_corr(portfolio, benchmark)
annualised volatility = sample_std(portfolio) × sqrt(252)
tracking error = sample_std(portfolio - benchmark) × sqrt(252)
```

A zero-variance benchmark is rejected. Alpha is omitted unless an annual
risk-free rate is supplied. The annual rate is converted geometrically:

```text
rf_daily = (1 + rf_annual)^(1/252) - 1
alpha_daily = mean((portfolio-rf_daily) - beta×(benchmark-rf_daily))
alpha_annual = 252 × alpha_daily
```

## Limitations

The models omit liquidity, transaction costs, changing historical weights,
issuer fundamentals, non-linear instruments, exchange-specific calendars,
volatility regimes, jumps, tail dependence, parameter uncertainty, and model
risk beyond the explicit checks described above. Overlapping historical returns
are dependent observations. Normal and log-normal assumptions can materially
understate real tail risk. Stress shocks are illustrative and not forecasts.
