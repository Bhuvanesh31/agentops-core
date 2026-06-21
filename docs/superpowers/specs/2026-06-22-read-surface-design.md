# Read Surface (Query API + Token Derivation) — Design Spec

**Date:** 2026-06-22
**Status:** Proposed (awaiting review)
**Builds on:** capture + capture-polish + run-timestamps (all merged to main)

## 1. Goal

Make the captured data **readable and queryable**, including token spend. Two
layers:

1. **Token derivation** — aggregate each run's token usage from
   `run_events.raw_payload` into the `usage_metrics` table, so the existing
   `run_overview` view (which already joins `usage_metrics`) returns real
   token numbers instead of NULLs.
2. **Read API** — JSON `GET` endpoints over `run_overview` and per-project
   rollups: list runs (filtered), run detail, and an overview rollup.

This is the first consumer-facing surface (north-star: *live agent-session
overview → token-spend visibility*). It is API-only and purely additive — no
frontend, no new dependencies.

**Out of scope (YAGNI):** any UI / HTML dashboard (separate later milestone);
estimated dollar `cost_usd` (deferred to the OTEL `reported` path); real-time
streaming / SSE; full per-event drill-down in run detail; `POST`/write changes
to runs.

## 2. Context & current state

- Read endpoints today: only `GET /repositories` and `GET /health`.
- `usage_metrics` table exists (one row per run, `run_id UNIQUE`) but is
  **empty (0 rows)**. Schema rule (verbatim): *missing values must remain
  NULL, must not be stored as zero.* Columns: `input_tokens`,
  `output_tokens`, `cached_input_tokens`, `cost_usd`, `cost_source`
  (`reported`/`estimated`/`unavailable`, default `unavailable`),
  `iteration_count`, `tool_calls_count`.
- `run_overview` view already joins `runs + projects + repositories + tools +
  outcomes + usage_metrics`; its token/cost columns are NULL only because
  `usage_metrics` is empty. Populating `usage_metrics` makes the view correct.
- Token data lives in `run_events.raw_payload->'message'->'usage'` on
  `assistant_message` events (9,748 events, all carrying `input_tokens` and
  `cache_read_input_tokens`).
- `started_at`/`ended_at` are now real session times (run-timestamps milestone),
  so time-based reads are meaningful.
- Event-type distribution: `assistant_message` 9748, `tool_use` 2526,
  `command_run` 1897, `file_edit` 640, `user_prompt` 637, `pr_link` 330,
  `session_started` 106, `session_ended` 101.

## 3. Decisions

- **Populate `usage_metrics` (derivation), not aggregate-on-read.** Uses the
  table the schema was built for; makes `run_overview` correct; keeps reads as
  simple `SELECT`s; `cost_source` supports the future OTEL `reported` upgrade.
- **Tokens only; no cost estimation now.** `cost_usd` stays NULL,
  `cost_source = 'unavailable'`. Authoritative dollar cost is the OTEL path's job.
- **Derivation runs both ways**, mirroring prior milestones: a go-forward
  incremental upsert on ingest, and an authoritative set-based backfill command.
- **Read API is the minimal trio:** `GET /runs`, `GET /runs/{run_id}`,
  `GET /overview`. Run detail returns the `run_overview` row only (no per-event
  list — that is a later need).

## 4. Token-derivation mapping

Per run, aggregated from its events:

| usage_metrics column | source |
| --- | --- |
| `input_tokens` | `Σ (message.usage.input_tokens)` over `assistant_message` events |
| `output_tokens` | `Σ (message.usage.output_tokens)` over `assistant_message` events |
| `cached_input_tokens` | `Σ (message.usage.cache_read_input_tokens)` over `assistant_message` events |
| `iteration_count` | count of `assistant_message` events |
| `tool_calls_count` | count of events with `event_type IN ('tool_use','command_run','file_edit')` |
| `cost_usd` | NULL |
| `cost_source` | `'unavailable'` |

NULL-not-zero: `SUM(...)` over zero matching rows yields NULL (correct — a run
with no `assistant_message` events has NULL token columns, not 0). Counts
(`iteration_count`, `tool_calls_count`) are legitimate integers and may be 0.

## 5. Architecture / components

### 5.1 Derivation (`app/models/ingestion.py`, `app/routes/events.py`)

- **Go-forward** — `record_event_usage(conn, *, run_id, event_type, usage)`:
  on a newly-inserted event, incrementally upsert the run's `usage_metrics`
  row (`INSERT … ON CONFLICT (run_id) DO UPDATE SET col = COALESCE(col,0) +
  delta, updated_at = NOW()`). An `assistant_message` with a `usage` block adds
  its `input`/`output`/`cache_read` tokens and `iteration_count += 1`; a
  `tool_use`/`command_run`/`file_edit` event adds `tool_calls_count += 1`;
  other events do nothing. Wired into `ingest_event` after `insert_run_event`,
  guarded by `not is_duplicate` (a duplicate's usage was already counted).
- **Backfill** — `backfill_usage_metrics(conn) -> int`: one set-based
  `INSERT INTO usage_metrics (...) SELECT run_id, <aggregations from §4> FROM
  run_events GROUP BY run_id ON CONFLICT (run_id) DO UPDATE SET … = EXCLUDED.…,
  updated_at = NOW()`, returning the row count. Authoritative: a re-run yields
  identical values (idempotent in result) and self-heals any go-forward drift.

### 5.2 Maintenance CLI (`app/maintenance.py`)

Restructure to argparse **subcommands** so the module can host multiple
maintenance ops:
- `python -m app.maintenance backfill-run-times` (the existing run-timestamp
  backfill).
- `python -m app.maintenance backfill-usage` (the new usage derivation).
The README's run-timestamps note is updated to the subcommand form.

### 5.3 Read models (`app/models/reads.py`, new)

- `list_runs(conn, *, project_id=None, repository_id=None, status=None,
  tool_id=None, limit, offset) -> list[dict]` — `SELECT … FROM run_overview`
  with optional equality filters, `ORDER BY started_at DESC`, `LIMIT/OFFSET`.
- `get_run(conn, run_id) -> dict | None` — one `run_overview` row.
- `project_overview(conn) -> list[dict]` — per project: `run_count`,
  `SUM(input_tokens)`, `SUM(output_tokens)`, `SUM(cached_input_tokens)`,
  `MIN(started_at)` as `earliest`, `MAX(COALESCE(ended_at, started_at))` as
  `latest_activity`.

### 5.4 Read routes (`app/routes/reads.py`, new) + schemas (`app/schemas/reads.py`, new)

- `GET /runs` — query params `project_id`, `repository_id`, `status`,
  `tool_id` (all optional), `limit` (default 50, max 200), `offset`
  (default 0). Returns `list[RunListItem]`.
- `GET /runs/{run_id}` — `RunDetail` (the `run_overview` row), or `404` if
  unknown. (Note: distinct from the existing `POST /runs/events`; different
  method/path, no conflict.)
- `GET /overview` — `list[ProjectOverview]`.
Schemas mirror the selected `run_overview` columns; token/cost fields are
`int | None` / `Decimal | None` to preserve NULLs.

## 6. Data flow

```
ingest (POST /runs/events)
  → get_or_create_run → insert_run_event → update_run_time_bounds (existing)
  → if not duplicate: record_event_usage   # incremental usage upsert

one-shot for existing data:
  python -m app.maintenance backfill-usage  # set-based aggregation -> usage_metrics

reads:
  GET /runs, /runs/{id}, /overview          # SELECT over run_overview / rollups
```

## 7. Error handling

- `GET /runs/{run_id}` unknown id → `404`.
- Empty result sets → `[]` (not an error).
- Invalid query params (e.g. `limit` over max, negative `offset`) → `422`
  (FastAPI validation via `Query(..., ge=, le=)`).
- NULL-not-zero preserved end to end: a run with no usage events serializes
  token fields as `null`, never `0`.
- Derivation casts JSON text to bigint; events lacking a `usage` block are
  excluded by the `assistant_message`/event-type filters, so no cast errors.

## 8. Testing

Live Postgres, `pytest-`-prefixed rows, synthetic data.

- **Derivation:** seed a run with known `assistant_message` usage events + tool
  events → backfill produces correct sums/counts; a run with no usage events →
  token columns NULL (not 0); backfill is idempotent in result (second run →
  identical values). Go-forward: ingesting usage events via the route
  accumulates the run's `usage_metrics`; duplicates don't double-count.
- **Endpoints:** `GET /runs` filters (by project/status) and pagination
  (`limit`/`offset`); `GET /runs/{id}` returns the row incl. token fields and
  `404`s on unknown; `GET /overview` rollups sum correctly across a project.
- **Real application:** run `backfill-usage` over the 9,748 events; verify
  per-project token totals are sane (non-NULL, plausible magnitude) and
  `GET /overview` reflects them.

## 9. Scope

**In scope (v1):** token derivation (go-forward + backfill) into
`usage_metrics`; `app/maintenance.py` subcommands; the three read endpoints +
schemas + read-model functions; real backfill of existing events; README
update.

**Out of scope:** UI/dashboard; estimated `cost_usd`; real-time/SSE; per-event
listing in run detail; write endpoints for runs; OTEL ingestion.

## 10. Assumptions

- `message.usage.input_tokens` / `output_tokens` / `cache_read_input_tokens`
  are present on `assistant_message` events (verified: 9748/9748).
- One `usage_metrics` row per run is sufficient (matches `run_id UNIQUE`);
  per-model or per-event breakdowns are not needed now.
- Current data volumes (≈106 runs, ≈16k events) make simple `run_overview`
  `SELECT`s performant without added indexes; revisit if volume grows.
