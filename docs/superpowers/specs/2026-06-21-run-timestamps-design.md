# Run Timestamps from Event Time — Design Spec

**Date:** 2026-06-21
**Status:** Proposed (awaiting review)
**Builds on:** Claude Code capture + capture-polish (PR #3, merged to main)

## 1. Goal

Make `runs.started_at` and `runs.ended_at` reflect the **real session time**
(derived from events' `occurred_at`) instead of ingestion/synthesis wall-clock
time. Two halves:

1. **Go-forward:** every ingest folds the event's `occurred_at` into the run's
   time bounds, so newly captured runs are correct.
2. **Backfill:** a one-shot host-local command recomputes the bounds for the
   runs already stored (106 today), so existing data is corrected without a
   full re-extraction.

**Out of scope (YAGNI):** a `duration` column; `status`/`session_ended`
reconciliation; an API endpoint for the backfill (host-local command only);
any change to `created_at`/`updated_at` (those correctly record write time).

## 2. Context & current state

- `runs.started_at TIMESTAMPTZ NOT NULL DEFAULT NOW()` — set by the schema
  default when `get_or_create_run` inserts a run, i.e. **ingestion time**, not
  session time. `get_or_create_run` never sets it explicitly.
- `runs.ended_at TIMESTAMPTZ` (nullable) — **never written today**; always NULL.
- `run_events.occurred_at TIMESTAMPTZ` carries the real per-event time from the
  transcript (nullable: some lines/synthesized events lack a time).
- The ingest path (`app/routes/events.py:ingest_event`) calls
  `get_or_create_run` then `insert_run_event(occurred_at=event.occurred_at)`,
  and never folds `occurred_at` back into the run.
- A run's true start = `MIN(occurred_at)` and true end = `MAX(occurred_at)`
  over its events. Events hang off the run (`run_id`), so this is computable.
- `created_at`/`updated_at` are bookkeeping and intentionally remain `NOW()`.

## 3. Decisions

- **Event-time vs processing-time split:** `started_at`/`ended_at` are domain
  facts about the session → derive from `occurred_at`. `created_at`/`updated_at`
  are write-time bookkeeping → keep `NOW()`.
- **Scope:** fix BOTH `started_at` and `ended_at` (same fold: MIN for start,
  MAX for end).
- **Coverage:** go-forward (durable) AND backfill the existing rows (so a full
  re-extraction is not required to correct history).
- **Go-forward folds only on NEWLY-INSERTED events**, not duplicates. A
  duplicate's `occurred_at` was already folded when first seen; `LEAST`/
  `GREATEST` are idempotent anyway, so skipping duplicates avoids needless
  writes without risk.
- **Backfill placement:** `app/maintenance.py` (a new tool-agnostic service
  maintenance entrypoint), NOT `capture/claude_code/` — correcting `runs`
  timestamps is a core-service concern, not Claude-Code-specific. The SQL
  helpers live in `app/models/ingestion.py` beside the other `runs` queries.

## 4. Architecture / components

### 4.1 Go-forward fold (`app/models/ingestion.py`, `app/routes/events.py`)

New model function:

```python
def update_run_time_bounds(
    conn: psycopg.Connection, *, run_id: UUID, occurred_at: datetime
) -> None:
    conn.execute(
        """
        UPDATE runs
        SET started_at = LEAST(started_at, %(occ)s),
            ended_at   = GREATEST(COALESCE(ended_at, %(occ)s), %(occ)s),
            updated_at = NOW()
        WHERE run_id = %(run_id)s
        """,
        {"occ": occurred_at, "run_id": run_id},
    )
```

- On a fresh run, `started_at` is the `NOW()` default; `LEAST(NOW(), occurred_at)`
  yields `occurred_at` (a past time) — snapping the bound back to reality.
- `COALESCE(ended_at, occ)` seeds `ended_at` from NULL on the first event;
  thereafter `GREATEST` extends it.

Call site in `app/routes/events.py:ingest_event`, after `insert_run_event`:
fold only when the event was newly inserted (`not is_duplicate`) **and**
`event.occurred_at is not None`.

### 4.2 Backfill (`app/models/ingestion.py`, `app/maintenance.py`)

New model function:

```python
def backfill_run_time_bounds(conn: psycopg.Connection) -> int:
    """Recompute started_at/ended_at from each run's events. Returns rows changed."""
    result = conn.execute(
        """
        UPDATE runs r
        SET started_at = sub.min_occ,
            ended_at   = sub.max_occ,
            updated_at = NOW()
        FROM (
            SELECT run_id,
                   MIN(occurred_at) AS min_occ,
                   MAX(occurred_at) AS max_occ
            FROM run_events
            WHERE occurred_at IS NOT NULL
            GROUP BY run_id
        ) sub
        WHERE r.run_id = sub.run_id
          AND (r.started_at IS DISTINCT FROM sub.min_occ
               OR r.ended_at IS DISTINCT FROM sub.max_occ)
        """
    )
    return result.rowcount
```

- `IS DISTINCT FROM` guard: only rows whose bounds actually change are written,
  so a second run reports 0 (idempotent) and NULL/value comparisons are safe.
- Runs with no timestamped events are not in the subquery → left untouched.

New `app/maintenance.py`:

```python
def main(argv: list[str] | None = None) -> int:
    # argparse (no required args for now); open get_connection(); run backfill; print count.
    with get_connection() as conn:
        changed = backfill_run_time_bounds(conn)
    print(f"runs updated: {changed}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
```

Run with `python -m app.maintenance`.

## 5. Data flow

```
event arrives (POST /runs/events)
  → get_or_create_run            # run exists or is created (started_at defaults to NOW())
  → insert_run_event(occurred_at)
  → if newly inserted AND occurred_at is not None:
        update_run_time_bounds   # LEAST/GREATEST fold -> real bounds

one-shot for the existing 106 runs:
  python -m app.maintenance      # MIN/MAX recompute from run_events, idempotent
```

## 6. Error handling / edge cases

- **`occurred_at is None`** on an event → go-forward skips the fold; backfill
  excludes it via `WHERE occurred_at IS NOT NULL`.
- **Run with zero timestamped events** → untouched: `started_at` keeps its
  `NOW()` default, `ended_at` stays NULL. Missing time data is left as-is, never
  fabricated.
- **Duplicate events** → go-forward does not fold (already folded on first
  insert); the operation is idempotent regardless.
- **Single-event run** → `started_at == ended_at` (correct: a point-in-time run).
- The fold runs inside the same request transaction as the event insert (the
  events route's `db_dependency` wraps `get_connection()`, which commits on
  clean exit and rolls back on error), so a failed event never leaves a
  half-updated bound.

## 7. Testing

Live Postgres, `pytest-`-prefixed rows, synthetic data.

- **Go-forward (events route / model):**
  - Ingest events with out-of-order `occurred_at` → run's `started_at` = min,
    `ended_at` = max.
  - A fresh run created by an event with a past `occurred_at` → `started_at`
    snaps from the `NOW()` default to that time (assert it is <= the event time,
    not ~NOW()).
  - An event with `occurred_at = None` does not raise and does not corrupt
    existing bounds.
- **Backfill (model):**
  - Seed a run whose `started_at` is wrong (e.g. NOW) with events at known
    times → backfill sets `started_at`/`ended_at` to min/max; a second run
    returns 0 (idempotent).
  - A run with no timestamped events is left untouched.
- **Real application:** run `python -m app.maintenance` against the live 106
  runs; verify `started_at` reflects real session times (earliest run dates back
  to ~2026-05-05 per capture history, not 2026-06) and `ended_at` is populated;
  idempotent re-run reports 0.

## 8. Scope

**In scope (v1):** `update_run_time_bounds` go-forward fold wired into the
events route; `backfill_run_time_bounds` + `app/maintenance.py` command;
tests; real backfill of the 106 runs; README note.

**Out of scope:** `duration` column; `status`/`session_ended` reconciliation;
backfill as an API endpoint; OTEL-sourced authoritative timing (future
enrichment); any change to `created_at`/`updated_at`.

## 9. Assumptions

- `occurred_at` values are trustworthy session times (they come from transcript
  line timestamps; verified during capture).
- The events route's per-request transaction is the right unit for the
  go-forward fold (one extra UPDATE per new timestamped event is acceptable
  overhead at current ingest volumes).
