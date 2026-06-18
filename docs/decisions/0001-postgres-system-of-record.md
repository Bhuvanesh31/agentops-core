# Decision 0001: PostgreSQL is the system of record

## Status

Accepted

## Decision

PostgreSQL will be the authoritative storage layer for AgentOps Core.

## Reason

Agent runs, events, repositories, commits, outcomes, and evaluation data are structured and relational.

PostgreSQL supports:

- Reliable transactions
- Structured filtering
- JSON payload storage
- Full-text search
- Referential integrity
- Reporting and analytics

## Other components

- Git provides provenance and fallback capture.
- Qdrant may provide semantic search.
- Qdrant must remain rebuildable from PostgreSQL.

## Rule

No consumer should treat Qdrant, Git, or local session files as the complete source of truth.
