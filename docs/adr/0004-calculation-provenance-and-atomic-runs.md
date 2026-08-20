# ADR 0004: Persist calculation provenance and atomic runs

- **Status:** Accepted

## Context

Risk values are ambiguous without input cutoff, model variant, parameters, versions, and run
status. Persisting result families incrementally could expose partial calculations as complete.

## Decision

Represent requests as runs with constrained pending, running, succeeded, and failed states.
Compute all result families before committing success atomically. Identify risk results using
run, method, variant, confidence, horizon, and a SHA-256 hash of canonical parameter JSON.
Record sanitized failure reasons separately.

## Consequences

Completed results are auditable and parameter variants cannot collide. Calculation uses more
memory before persistence, and schema changes require explicit migrations.

## Rejected alternatives

- Overwriting one result row per method.
- Marking success before every result family is persisted.
- Storing raw exception paths or omitting failed runs.
