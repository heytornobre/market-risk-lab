# ADR 0002: Use deterministic synthetic data

- **Status:** Accepted

## Context

A public risk engine needs credible multi-asset, price, and FX behavior without exposing or
approximating a real portfolio. Tests and screenshots also need stable expected values.

## Decision

Generate fictional instruments, positions, prices, and FX rates from a versioned TOML
specification and fixed seed. Fix ordering, precision, encoding, and line endings, and validate
the generated bundle before loading it.

## Consequences

Runs are reproducible under the dependency lock and calculation version. The data exercises
engineering paths but cannot support empirical market or investment claims.

## Rejected alternatives

- Anonymising, perturbing, or rescaling private holdings.
- Downloading live market data for the MVP.
- Committing arbitrary hand-edited fixtures without a reproducible generator.
