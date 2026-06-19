# Decision 0002: FastAPI ingestion service with raw-SQL persistence

## Status

Accepted

## Decision

The ingestion layer is a FastAPI application that writes to PostgreSQL using
psycopg 3 with hand-written, parameterized SQL. No ORM is introduced.

## Reason

- The PostgreSQL schema (`database/schema.sql`) is the authoritative model and
  already encodes the important invariants as `UNIQUE` and `CHECK` constraints.
  Raw SQL maps directly onto it without a second, divergent model definition.
- Two existing constraints carry the core ingestion semantics:
  - `runs_tool_repository_session_unique (tool_id, repository_id, session_id)`
    backs *create-or-locate run*.
  - `run_events_source_event_unique (source_event_id)` backs *idempotent
    ingestion* via `INSERT ... ON CONFLICT DO NOTHING`.
- Synchronous endpoints over a `psycopg_pool` connection pool keep the code and
  the tests simple. FastAPI runs sync routes in a worker threadpool.

## Structure

- `app/config.py` — settings from environment / `.env`.
- `app/database.py` — connection pool + request-scoped connection dependency.
- `app/schemas/` — Pydantic request/response models (the normalized event).
- `app/models/` — data-access layer (raw SQL), not ORM classes.
- `app/routes/` — `GET /health`, `POST /runs/events`.
- `app/redaction.py` — secret redaction applied before persistence.

## Redaction

Payloads are redacted before storage using two strategies: sensitive-key
matching and secret-shaped value matching, applied recursively. The event's
`redaction_status` records whether anything was changed.

## Consequences

- Adding fields means editing SQL in `app/models/ingestion.py` directly.
- Transaction boundaries are explicit: the pooled connection commits on a clean
  response and rolls back on error, so an event persists fully or not at all.
- The redaction pattern set is a denylist and will need ongoing extension; it is
  isolated in one module to make that easy.
