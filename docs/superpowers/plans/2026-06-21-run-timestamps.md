# Run Timestamps from Event Time — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `runs.started_at`/`runs.ended_at` reflect real session time (from events' `occurred_at`) instead of ingestion wall-clock time — both for new ingests (go-forward fold) and the runs already stored (one-shot backfill).

**Architecture:** A new model function folds each event's `occurred_at` into the run's `started_at` (LEAST) and `ended_at` (GREATEST) during ingestion; a second model function plus a host-local `app/maintenance.py` command recomputes those bounds from `run_events` for existing rows. `created_at`/`updated_at` are unchanged (correct as `NOW()`).

**Tech Stack:** Python 3.12, FastAPI, psycopg 3 (raw SQL, no ORM), pytest, ruff.

## Global Constraints

- Python `>=3.12`; psycopg 3 raw parameterized SQL, no ORM.
- ruff: `line-length = 100`, lint select `E, F, I, UP, B`.
- Tests run against the live Postgres container; create rows with ids prefixed `pytest-`; sanitized synthetic data.
- `db_dependency` wraps `get_connection()` (commits on clean exit, rolls back on error) — one transaction per request.
- The events endpoint route is `POST /runs/events`. Seeded ids for tests: project `agentops-core`, repository `agentops-core-main`, tool `claude-code`.
- Commit messages end with: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
- Branch: `feat/run-timestamps`.
- Missing time data is left as-is, never fabricated: events with `occurred_at IS NULL` are skipped; runs with no timestamped events keep their defaults.

---

### Task 1: Go-forward fold — `update_run_time_bounds` wired into ingestion

**Files:**
- Modify: `app/models/ingestion.py`
- Modify: `app/routes/events.py`
- Test: `tests/test_run_time_bounds.py`

**Interfaces:**
- Produces: `ingestion.update_run_time_bounds(conn, *, run_id: UUID, occurred_at: datetime) -> None` — folds one event time into the run's bounds via `LEAST`/`GREATEST`.
- Consumes (existing): the `ingest_event` route already has `run_id`, `is_duplicate`, and `event.occurred_at` in scope after `insert_run_event`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_run_time_bounds.py
from datetime import datetime, timezone
from uuid import uuid4

from app.database import get_connection
from tests.conftest import make_event


def _run_bounds(run_id: str) -> tuple:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT started_at, ended_at FROM runs WHERE run_id = %s", (run_id,)
        ).fetchone()
    return row["started_at"], row["ended_at"]


def test_started_and_ended_track_event_times(client):
    session_id = f"pytest-session-{uuid4().hex}"
    early = "2026-05-01T10:00:00+00:00"
    late = "2026-05-01T12:30:00+00:00"
    # Post the LATER event first to prove ordering does not matter.
    r1 = client.post(
        "/runs/events",
        json=make_event(session_id=session_id, occurred_at=late,
                        source_event_id=f"pytest-event-{uuid4().hex}"),
    )
    client.post(
        "/runs/events",
        json=make_event(session_id=session_id, event_type="file_edited",
                        occurred_at=early, source_event_id=f"pytest-event-{uuid4().hex}"),
    )
    run_id = r1.json()["run_id"]
    started, ended = _run_bounds(run_id)
    assert started == datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc)
    assert ended == datetime(2026, 5, 1, 12, 30, tzinfo=timezone.utc)


def test_fresh_run_started_at_snaps_back_from_now(client):
    # A new run's started_at defaults to NOW(); a past occurred_at must lower it.
    session_id = f"pytest-session-{uuid4().hex}"
    past = datetime(2026, 5, 1, 9, 0, tzinfo=timezone.utc)
    client.post(
        "/runs/events",
        json=make_event(session_id=session_id, occurred_at=past.isoformat(),
                        source_event_id=f"pytest-event-{uuid4().hex}"),
    )
    with get_connection() as conn:
        row = conn.execute(
            "SELECT started_at FROM runs WHERE session_id = %s", (session_id,)
        ).fetchone()
    assert row["started_at"] == past  # not ~NOW()


def test_event_without_occurred_at_does_not_break(client):
    session_id = f"pytest-session-{uuid4().hex}"
    # occurred_at omitted -> None; must not raise and run still created.
    resp = client.post(
        "/runs/events",
        json=make_event(session_id=session_id,
                        source_event_id=f"pytest-event-{uuid4().hex}"),
    )
    assert resp.status_code == 201
    started, ended = _run_bounds(resp.json()["run_id"])
    assert started is not None  # NOW() default retained
    assert ended is None        # no event time to set it
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `set -a; . ./.env; set +a; .venv/bin/pytest tests/test_run_time_bounds.py -v`
Expected: FAIL — `test_started_and_ended_track_event_times` and `test_fresh_run_started_at_snaps_back_from_now` fail (bounds reflect `NOW()` / `ended_at` is NULL) because the fold isn't wired yet. (`test_event_without_occurred_at_does_not_break` may already pass.)

- [ ] **Step 3: Add `update_run_time_bounds` to the model layer**

Append to `app/models/ingestion.py` (the `datetime` and `UUID` imports already exist at the top of the file):

```python
def update_run_time_bounds(
    conn: psycopg.Connection, *, run_id: UUID, occurred_at: datetime
) -> None:
    """Fold one event's occurred_at into the run's started_at/ended_at.

    started_at takes the earliest time seen (LEAST against the NOW() default on a
    fresh run); ended_at takes the latest (COALESCE seeds it from NULL on the
    first event, GREATEST extends it thereafter).
    """
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

- [ ] **Step 4: Wire the fold into the events route**

In `app/routes/events.py:ingest_event`, immediately after the `event_id, is_duplicate = ingestion.insert_run_event(...)` call (after its closing paren) and before the `response.status_code = ...` line, add:

```python
    if not is_duplicate and event.occurred_at is not None:
        ingestion.update_run_time_bounds(
            conn, run_id=run_id, occurred_at=event.occurred_at
        )
```

(Fold only on newly-inserted events; a duplicate's time was already folded when first seen.)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `set -a; . ./.env; set +a; .venv/bin/pytest tests/test_run_time_bounds.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Run the broader event suite (no regressions)**

Run: `set -a; . ./.env; set +a; .venv/bin/pytest tests/test_events.py -v`
Expected: PASS (all existing event tests still green).

- [ ] **Step 7: Lint and commit**

```bash
.venv/bin/ruff format app tests
.venv/bin/ruff check app tests
git add app/models/ingestion.py app/routes/events.py tests/test_run_time_bounds.py
git commit -m "feat: fold event occurred_at into run started_at/ended_at on ingest

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Backfill — `backfill_run_time_bounds` + `app/maintenance.py` command

**Files:**
- Modify: `app/models/ingestion.py`
- Create: `app/maintenance.py`
- Test: `tests/test_maintenance.py`

**Interfaces:**
- Consumes: `ingestion.update_run_time_bounds` is unrelated; this task only uses existing `get_or_create_run`, `insert_run_event`, and `get_connection`.
- Produces:
  - `ingestion.backfill_run_time_bounds(conn) -> int` — recompute bounds for all runs from `run_events`; returns rows changed.
  - `app.maintenance.main(argv: list[str] | None = None) -> int` — open a connection, run the backfill, print `runs updated: <n>`, return 0.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_maintenance.py
from datetime import datetime, timezone
from uuid import uuid4

from app import maintenance
from app.database import get_connection
from app.models import ingestion

P, R, T = "agentops-core", "agentops-core-main", "claude-code"


def _seed_run_with_events(conn, session_id, times):
    run_id, _ = ingestion.get_or_create_run(
        conn, project_id=P, repository_id=R, tool_id=T, session_id=session_id,
        model="claude-opus-4-8", branch="main", cwd="/tmp/work", intent=None,
    )
    for ts in times:
        ingestion.insert_run_event(
            conn, source_event_id=f"pytest-event-{uuid4().hex}", run_id=run_id,
            tool_id=T, session_id=session_id, event_type="file_edited",
            files_touched=[], raw_payload={}, redaction_status="clean", occurred_at=ts,
        )
    return run_id


def test_backfill_sets_min_and_max_from_events():
    session_id = f"pytest-session-{uuid4().hex}"
    t_early = datetime(2026, 5, 2, 8, 0, tzinfo=timezone.utc)
    t_late = datetime(2026, 5, 2, 11, 0, tzinfo=timezone.utc)
    with get_connection() as conn:
        run_id = _seed_run_with_events(conn, session_id, [t_late, t_early])
        # Force a wrong started_at and NULL ended_at to simulate pre-fix data.
        conn.execute(
            "UPDATE runs SET started_at = NOW(), ended_at = NULL WHERE run_id = %s",
            (run_id,),
        )
    with get_connection() as conn:
        changed = ingestion.backfill_run_time_bounds(conn)
    assert changed >= 1
    with get_connection() as conn:
        row = conn.execute(
            "SELECT started_at, ended_at FROM runs WHERE run_id = %s", (run_id,)
        ).fetchone()
    assert row["started_at"] == t_early
    assert row["ended_at"] == t_late


def test_backfill_is_idempotent():
    session_id = f"pytest-session-{uuid4().hex}"
    t = datetime(2026, 5, 3, 9, 0, tzinfo=timezone.utc)
    with get_connection() as conn:
        run_id = _seed_run_with_events(conn, session_id, [t])
        conn.execute("UPDATE runs SET started_at = NOW(), ended_at = NULL WHERE run_id = %s", (run_id,))
    with get_connection() as conn:
        first = ingestion.backfill_run_time_bounds(conn)
    with get_connection() as conn:
        second = ingestion.backfill_run_time_bounds(conn)
    assert first >= 1
    # Second pass changes nothing for the already-correct row.
    with get_connection() as conn:
        row = conn.execute(
            "SELECT started_at, ended_at FROM runs WHERE run_id = %s", (run_id,)
        ).fetchone()
    assert row["started_at"] == t and row["ended_at"] == t
    # second may be 0 globally only if no other run needed fixing; assert our row stable instead.


def test_main_runs_and_returns_zero(capsys):
    rc = maintenance.main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "runs updated:" in out
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `set -a; . ./.env; set +a; .venv/bin/pytest tests/test_maintenance.py -v`
Expected: FAIL — `app.maintenance` module does not exist / `backfill_run_time_bounds` undefined.

- [ ] **Step 3: Add `backfill_run_time_bounds` to the model layer**

Append to `app/models/ingestion.py`:

```python
def backfill_run_time_bounds(conn: psycopg.Connection) -> int:
    """Recompute started_at/ended_at for all runs from their events.

    Uses MIN/MAX of run_events.occurred_at (ignoring NULLs). The IS DISTINCT FROM
    guard updates only rows whose bounds actually change, so re-runs are no-ops.
    Returns the number of rows updated.
    """
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

- [ ] **Step 4: Create the maintenance command**

```python
# app/maintenance.py
"""Host-local maintenance commands for the AgentOps service database.

Run with: python -m app.maintenance
Currently: recompute runs.started_at/ended_at from event times (backfill).
"""

import argparse

from app.database import get_connection
from app.models.ingestion import backfill_run_time_bounds


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Recompute runs.started_at/ended_at from run_events.occurred_at"
    )
    parser.parse_args(argv)

    with get_connection() as conn:
        changed = backfill_run_time_bounds(conn)
    print(f"runs updated: {changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `set -a; . ./.env; set +a; .venv/bin/pytest tests/test_maintenance.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Lint and commit**

```bash
.venv/bin/ruff format app tests
.venv/bin/ruff check app tests
git add app/models/ingestion.py app/maintenance.py tests/test_maintenance.py
git commit -m "feat: backfill_run_time_bounds + app.maintenance command

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Apply backfill to real data + document

**Files:**
- Modify: `README.md`

**Interfaces:** none (operational + docs).

- [ ] **Step 1: Snapshot the current (wrong) bounds**

Run:
```bash
set -a; . ./.env; set +a
docker exec agentops-postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc \
  "SELECT 'min_started='||min(started_at)||' max_started='||max(started_at) FROM runs;
   SELECT 'ended_null='||count(*) FILTER (WHERE ended_at IS NULL)||' of '||count(*) FROM runs;"
```
Expected (pre-fix): `started_at` clustered around the ingestion dates (2026-06), and most/all `ended_at` NULL.

- [ ] **Step 2: Run the backfill on real data**

Run:
```bash
set -a; . ./.env; set +a
.venv/bin/python -m app.maintenance
```
Expected: `runs updated: <n>` where n is the number of runs with timestamped events (up to 106).

- [ ] **Step 3: Verify real session times now present**

Run:
```bash
set -a; . ./.env; set +a
docker exec agentops-postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc \
  "SELECT 'earliest_started='||min(started_at) FROM runs WHERE ended_at IS NOT NULL;
   SELECT 'ended_null='||count(*) FILTER (WHERE ended_at IS NULL)||' of '||count(*) FROM runs;
   SELECT 'started_after_ended='||count(*) FROM runs WHERE ended_at IS NOT NULL AND started_at > ended_at;"
```
Expected: earliest `started_at` dates back to real session history (~2026-05-05, not 2026-06); `ended_null` drops to only runs with no timestamped events; `started_after_ended` = 0 (invariant: start never after end).

- [ ] **Step 4: Confirm idempotency on real data**

Run:
```bash
set -a; . ./.env; set +a
.venv/bin/python -m app.maintenance
```
Expected: `runs updated: 0`.

- [ ] **Step 5: Document in the README**

Append to the Claude Code capture / runs section of `README.md`:

```markdown
### Run timestamps

`runs.started_at` and `runs.ended_at` reflect real session time, derived from
events' `occurred_at`: each ingest folds the event time into the run's bounds
(`started_at = min`, `ended_at = max`), and `created_at`/`updated_at` remain
write-time bookkeeping. To recompute the bounds for runs already stored (e.g.
after importing historical events), run the host-local command:
`python -m app.maintenance`. It is idempotent — a second run updates 0 rows.
```

- [ ] **Step 6: Commit the docs**

```bash
git add README.md
git commit -m "docs: document run timestamps + app.maintenance backfill

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**1. Spec coverage:**
- §4.1 go-forward fold (`update_run_time_bounds`, LEAST/GREATEST/COALESCE, wired into events route, only new + non-null) → Task 1. ✓
- §4.2 backfill (`backfill_run_time_bounds`, MIN/MAX, IS DISTINCT FROM, `app/maintenance.py`, `python -m app.maintenance`) → Task 2. ✓
- §5 data flow (fold after insert on new+timestamped events; one-shot backfill) → Tasks 1 + 3. ✓
- §6 edge cases (null occurred_at skipped both paths; no-event run untouched; duplicates not re-folded; single-event start==end) → Task 1 tests (null), Task 2 (MIN/MAX excludes null), Task 1 route guard (duplicates). ✓
- §7 testing (out-of-order min/max; fresh-run snap-back; null safe; backfill wrong→corrected + idempotent; real application + verify) → Tasks 1, 2, 3. ✓
- §8 scope (no duration col, no status reconciliation, no API endpoint, created/updated_at untouched) → not implemented anywhere. ✓

**2. Placeholder scan:** No TBD/TODO; every code step has complete code; every command has expected output; the one operational `<n>` is explained, not a placeholder. ✓

**3. Type consistency:** `update_run_time_bounds(conn, *, run_id: UUID, occurred_at: datetime) -> None` and `backfill_run_time_bounds(conn) -> int` are consistent across the model, the route call site, the maintenance command, and the tests. The route call passes `run_id=run_id` (a UUID from `get_or_create_run`) and `occurred_at=event.occurred_at` (a `datetime | None`, guarded non-None before the call). `app.maintenance.main(argv)` matches the test's `maintenance.main([])`. ✓

> Note on `test_backfill_is_idempotent`: it asserts the target row's bounds are stable after a second pass (rather than asserting a global return of 0), because other rows in a shared live DB could legitimately need updating. This is intentional and correct for a shared-database test.
