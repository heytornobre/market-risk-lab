# ADR 0001: Create a clean public history

- **Status:** Accepted

## Context

The source-of-truth project is private and may contain confidential data, identifiers,
artifacts, binary metadata, and historical Git objects. Removing files from its latest tree
would not remove them from history.

## Decision

Build `market-risk-lab` as a separate candidate with a future fresh Git history. Reuse is
allowed only after explicit file review and public-safety approval; this MVP independently
uses synthetic data and contains no private-source adaptation.

## Consequences

The private repository remains unchanged, provenance across repositories is intentionally not
preserved, and publication requires scanning the complete new history and source archive.

## Rejected alternatives

- Making the private repository public after deleting current files.
- Rewriting the private repository's history and accepting residual-disclosure risk.
