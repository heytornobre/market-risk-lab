# ADR 0003: Use SQLite as the MVP storage boundary

- **Status:** Accepted

## Context

The portfolio project needs explicit schemas, migrations, transactions, and inspectable local
state without cloud provisioning or dual-storage complexity.

## Decision

Use SQLite exclusively for the MVP. Apply ordered migrations, enable foreign keys, load
fixtures idempotently, and keep generated databases outside the publishable tree.

## Consequences

The complete demo runs offline with minimal setup and demonstrates transactional persistence.
It does not demonstrate distributed concurrency, remote durability, or service operation.

## Rejected alternatives

- Adding Turso/libSQL before the local contracts are stable.
- Using unversioned tables created implicitly by application queries.
- Treating flat files as the result store.
