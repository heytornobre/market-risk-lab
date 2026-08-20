# ADR 0005: Keep the dashboard read-only

- **Status:** Accepted

## Context

The dashboard should explain persisted synthetic results without becoming a second
orchestration surface or creating hidden database side effects.

## Decision

Treat Streamlit as a presentation adapter. Open an existing SQLite file with `mode=ro`,
`immutable=1`, and `query_only`; select only succeeded runs; validate result completeness; and
keep query/view-model construction separate from rendering. Refresh is explicit.

## Consequences

Dashboard interaction cannot generate, migrate, load, calculate, change statuses, or write
journals. Users must prepare the database through explicit CLI commands, and stale or invalid
states produce actionable errors instead of being repaired implicitly.

## Rejected alternatives

- A “run calculations” dashboard button.
- Opening SQLite through the write-capable repository connection.
- Auto-refresh loops or cached live connections.
