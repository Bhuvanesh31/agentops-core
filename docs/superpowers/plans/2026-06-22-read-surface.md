# Read Surface (Query API + Token Derivation) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make captured data queryable — derive per-run token usage into `usage_metrics` (so `run_overview` returns real numbers), then expose JSON read endpoints: `GET /runs`, `GET /runs/{run_id}`, `GET /overview`.

**Architecture:** A derivation layer aggregates `run_events.raw_payload` token usage into `usage_metrics` (go-forward incremental upsert on ingest + an authoritative set-based backfill command). A read API of `GET` endpoints selects from the existing `run_overview` view and per-project rollups. Additive only; no frontend, no new dependencies.

**Tech Stack:** Python 3.12, FastAPI, psycopg 3 (raw SQL, no ORM), pydantic, pytest, ruff.

## Global Constraints

- Python `>=3.12`; psycopg 3 raw parameterized SQL, no ORM.
- ruff: `line-length = 100`, lint select `E, F, I, UP, B`.
- Tests run against live Postgres; rows prefixed `pytest-`; sanitized synthetic data.
- `usage_metrics` rule (schema): **missing values stay NULL, never 0.** `cost_usd` stays NULL, `cost_source = 'unavailable'` (tokens-only; no dollar estimation).
- Token derivation mapping (per run): `input_tokens = Σ usage.input_tokens`, `output_tokens = Σ usage.output_tokens`, `cached_input_tokens = Σ usage.cache_read_input_tokens` over `assistant_message` events; `iteration_count = count(assistant_message)`; `tool_calls_count = count(event_type IN ('tool_use','command_run','file_edit'))`. Token usage JSON path: `raw_payload->'message'->'usage'->>'<field>'`.
- `db_dependency` wraps `get_connection()` (commits clean / rolls back on error). Whole-table backfill functions, when called from tests against the shared live DB, must be exercised inside a transaction that is **rolled back** (do not persist to shared data).
- The events endpoint is `POST /runs/events`; new read endpoints are `GET /runs`, `GET /runs/{run_id}`, `GET /overview`. Seeded test ids: project `agentops-core`, repo `agentops-core-main`, tool `claude-code`.
- Commit messages end with: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
- Branch: `feat/read-surface`.

---

### Task 1: Usage backfill + maintenance subcommands

**Files:**
- Modify: `app/models/ingestion.py`
- Modify: `app/maintenance.py`
- Modify: `README.md`
- Test: `tests/test_maintenance.py`

**Interfaces:**
- Produces: `ingestion.backfill_usage_metrics(conn) -> int` — set-based aggregation of token usage into `usage_metrics`; returns rows written. Idempotent in result.
- Consumes (existing): `ingestion.backfill_run_time_bounds`, `app.database.get_connection`, `ingestion.get_or_create_run`, `ingestion.insert_run_event`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_maintenance.py — add (keep existing imports; add as needed)
from uuid import uuid4

from app import maintenance
from app.database import get_connection
from app.models import ingestion

P, R, T = "agentops-core", "agentops-core-main", "claude-code"


def _seed_run(conn, session_id):
    run_id, _ = ingestion.get_or_create_run(
        conn, project_id=P, repository_id=R, tool_id=T, session_id=session_id,
        model="claude-opus-4-8", branch="main", cwd="/tmp/work", intent=None,
    )
    return run_id


def _seed_event(conn, run_id, session_id, event_type, usage=None):
    payload = {"message": {"usage": usage}} if usage is not None else {}
    ingestion.insert_run_event(
        conn, source_event_id=f"pytest-event-{uuid4().hex}", run_id=run_id,
        tool_id=T, session_id=session_id, event_type=event_type,
        files_touched=[], raw_payload=payload, redaction_status="clean", occurred_at=None,
    )


def test_backfill_usage_aggregates_tokens_and_counts():
    sid = f"pytest-session-{uuid4().hex}"
    with get_connection() as conn:
        run_id = _seed_run(conn, sid)
        _seed_event(conn, run_id, sid, "assistant_message",
                    {"input_tokens": 100, "output_tokens": 40, "cache_read_input_tokens": 5})
        _seed_event(conn, run_id, sid, "assistant_message",
                    {"input_tokens": 10, "output_tokens": 4, "cache_read_input_tokens": 1})
        _seed_event(conn, run_id, sid, "tool_use")
        _seed_event(conn, run_id, sid, "command_run")
        ingestion.backfill_usage_metrics(conn)
        row = conn.execute(
            "SELECT input_tokens, output_tokens, cached_input_tokens, iteration_count, "
            "tool_calls_count, cost_usd, cost_source FROM usage_metrics WHERE run_id = %s",
            (run_id,),
        ).fetchone()
        conn.rollback()
    assert row["input_tokens"] == 110
    assert row["output_tokens"] == 44
    assert row["cached_input_tokens"] == 6
    assert row["iteration_count"] == 2
    assert row["tool_calls_count"] == 2
    assert row["cost_usd"] is None
    assert row["cost_source"] == "unavailable"


def test_backfill_usage_null_not_zero_for_no_usage_run():
    sid = f"pytest-session-{uuid4().hex}"
    with get_connection() as conn:
        run_id = _seed_run(conn, sid)
        _seed_event(conn, run_id, sid, "user_prompt")  # no usage, not a tool call
        ingestion.backfill_usage_metrics(conn)
        row = conn.execute(
            "SELECT input_tokens, output_tokens, iteration_count, tool_calls_count "
            "FROM usage_metrics WHERE run_id = %s", (run_id,),
        ).fetchone()
        conn.rollback()
    assert row["input_tokens"] is None       # NULL, not 0
    assert row["output_tokens"] is None
    assert row["iteration_count"] == 0       # a count, legitimately 0
    assert row["tool_calls_count"] == 0


def test_backfill_usage_idempotent_in_result():
    sid = f"pytest-session-{uuid4().hex}"
    with get_connection() as conn:
        run_id = _seed_run(conn, sid)
        _seed_event(conn, run_id, sid, "assistant_message",
                    {"input_tokens": 7, "output_tokens": 3, "cache_read_input_tokens": 0})
        ingestion.backfill_usage_metrics(conn)
        ingestion.backfill_usage_metrics(conn)  # second pass
        row = conn.execute(
            "SELECT input_tokens, iteration_count FROM usage_metrics WHERE run_id = %s",
            (run_id,),
        ).fetchone()
        conn.rollback()
    assert row["input_tokens"] == 7          # stable, not doubled
    assert row["iteration_count"] == 1


def test_main_backfill_run_times_subcommand(capsys):
    rc = maintenance.main(["backfill-run-times"])
    assert rc == 0
    assert "runs updated:" in capsys.readouterr().out


def test_main_backfill_usage_subcommand(capsys):
    rc = maintenance.main(["backfill-usage"])
    assert rc == 0
    assert "usage rows written:" in capsys.readouterr().out
```

(Delete/replace any prior `test_main_runs_and_returns_zero` that calls `maintenance.main([])` — the bare invocation no longer exists once subcommands are required.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `set -a; . ./.env; set +a; .venv/bin/pytest tests/test_maintenance.py -v`
Expected: FAIL — `backfill_usage_metrics` undefined; `main(["backfill-usage"])` errors (no subcommands yet).

- [ ] **Step 3: Add `backfill_usage_metrics` to the model layer**

Append to `app/models/ingestion.py`:

```python
def backfill_usage_metrics(conn: psycopg.Connection) -> int:
    """Aggregate per-run token usage from run_events into usage_metrics.

    Sums token counts from assistant_message events' raw_payload.message.usage,
    counts iterations (assistant_message) and tool calls (tool_use/command_run/
    file_edit). cost_usd/cost_source are left untouched on conflict so a future
    'reported' cost is never clobbered. Idempotent in result. Returns rows written.
    """
    result = conn.execute(
        """
        INSERT INTO usage_metrics (
            run_id, input_tokens, output_tokens, cached_input_tokens,
            iteration_count, tool_calls_count, cost_source
        )
        SELECT
            run_id,
            SUM((raw_payload->'message'->'usage'->>'input_tokens')::bigint)
                FILTER (WHERE event_type = 'assistant_message'),
            SUM((raw_payload->'message'->'usage'->>'output_tokens')::bigint)
                FILTER (WHERE event_type = 'assistant_message'),
            SUM((raw_payload->'message'->'usage'->>'cache_read_input_tokens')::bigint)
                FILTER (WHERE event_type = 'assistant_message'),
            COUNT(*) FILTER (WHERE event_type = 'assistant_message'),
            COUNT(*) FILTER (WHERE event_type IN ('tool_use', 'command_run', 'file_edit')),
            'unavailable'
        FROM run_events
        GROUP BY run_id
        ON CONFLICT (run_id) DO UPDATE SET
            input_tokens        = EXCLUDED.input_tokens,
            output_tokens       = EXCLUDED.output_tokens,
            cached_input_tokens = EXCLUDED.cached_input_tokens,
            iteration_count     = EXCLUDED.iteration_count,
            tool_calls_count    = EXCLUDED.tool_calls_count,
            updated_at          = NOW()
        """
    )
    return result.rowcount
```

- [ ] **Step 4: Restructure `app/maintenance.py` to subcommands**

Replace the file with:

```python
"""Host-local maintenance commands for the AgentOps service database.

Usage:
  python -m app.maintenance backfill-run-times   # runs.started_at/ended_at from event times
  python -m app.maintenance backfill-usage       # token usage from run_events -> usage_metrics
"""

import argparse

from app.database import get_connection
from app.models.ingestion import backfill_run_time_bounds, backfill_usage_metrics


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AgentOps service maintenance commands")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser(
        "backfill-run-times",
        help="Recompute runs.started_at/ended_at from run_events.occurred_at",
    )
    sub.add_parser(
        "backfill-usage",
        help="Aggregate run_events token usage into usage_metrics",
    )
    args = parser.parse_args(argv)

    with get_connection() as conn:
        if args.command == "backfill-run-times":
            print(f"runs updated: {backfill_run_time_bounds(conn)}")
        elif args.command == "backfill-usage":
            print(f"usage rows written: {backfill_usage_metrics(conn)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Update the run-timestamps README note to the subcommand form**

In `README.md`, change the run-timestamps note command from
`python -m app.maintenance` to `python -m app.maintenance backfill-run-times`.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `set -a; . ./.env; set +a; .venv/bin/pytest tests/test_maintenance.py -v`
Expected: PASS (5 tests).

- [ ] **Step 7: Lint and commit**

```bash
.venv/bin/ruff format app tests
.venv/bin/ruff check app tests
git add app/models/ingestion.py app/maintenance.py README.md tests/test_maintenance.py
git commit -m "feat: backfill_usage_metrics + maintenance subcommands

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Go-forward usage upsert into the events route

**Files:**
- Modify: `app/models/ingestion.py`
- Modify: `app/routes/events.py`
- Test: `tests/test_usage_goforward.py`

**Interfaces:**
- Produces: `ingestion.record_event_usage(conn, *, run_id: UUID, event_type: str, usage: dict | None) -> None` — incremental per-event upsert into `usage_metrics`.
- Consumes: the `ingest_event` route's `run_id`, `is_duplicate`, `event.event_type`, `event.raw_payload`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_usage_goforward.py
from uuid import uuid4

from app.database import get_connection
from tests.conftest import make_event


def _usage_row(run_id):
    with get_connection() as conn:
        return conn.execute(
            "SELECT input_tokens, output_tokens, cached_input_tokens, iteration_count, "
            "tool_calls_count FROM usage_metrics WHERE run_id = %s", (run_id,),
        ).fetchone()


def _assistant_payload(inp, out, cache):
    return {"message": {"usage": {"input_tokens": inp, "output_tokens": out,
                                  "cache_read_input_tokens": cache}}}


def test_goforward_accumulates_usage(client):
    sid = f"pytest-session-{uuid4().hex}"
    r1 = client.post("/runs/events", json=make_event(
        session_id=sid, event_type="assistant_message",
        raw_payload=_assistant_payload(100, 40, 5),
        source_event_id=f"pytest-event-{uuid4().hex}"))
    client.post("/runs/events", json=make_event(
        session_id=sid, event_type="assistant_message",
        raw_payload=_assistant_payload(10, 4, 1),
        source_event_id=f"pytest-event-{uuid4().hex}"))
    client.post("/runs/events", json=make_event(
        session_id=sid, event_type="tool_use", raw_payload={},
        source_event_id=f"pytest-event-{uuid4().hex}"))
    row = _usage_row(r1.json()["run_id"])
    assert row["input_tokens"] == 110
    assert row["output_tokens"] == 44
    assert row["cached_input_tokens"] == 6
    assert row["iteration_count"] == 2
    assert row["tool_calls_count"] == 1


def test_goforward_duplicate_not_double_counted(client):
    sid = f"pytest-session-{uuid4().hex}"
    seid = f"pytest-event-{uuid4().hex}"
    payload = make_event(session_id=sid, event_type="assistant_message",
                         raw_payload=_assistant_payload(50, 20, 0), source_event_id=seid)
    r1 = client.post("/runs/events", json=payload)
    client.post("/runs/events", json=payload)  # duplicate source_event_id
    row = _usage_row(r1.json()["run_id"])
    assert row["input_tokens"] == 50   # not 100
    assert row["iteration_count"] == 1


def test_goforward_non_usage_event_leaves_tokens_null(client):
    sid = f"pytest-session-{uuid4().hex}"
    r1 = client.post("/runs/events", json=make_event(
        session_id=sid, event_type="user_prompt", raw_payload={},
        source_event_id=f"pytest-event-{uuid4().hex}"))
    row = _usage_row(r1.json()["run_id"])
    # No usage_metrics row at all (no usage/tool events) -> None
    assert row is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `set -a; . ./.env; set +a; .venv/bin/pytest tests/test_usage_goforward.py -v`
Expected: FAIL — usage not recorded (no `usage_metrics` rows from ingest).

- [ ] **Step 3: Add `record_event_usage` to the model layer**

Append to `app/models/ingestion.py`:

```python
def record_event_usage(
    conn: psycopg.Connection, *, run_id: UUID, event_type: str, usage: dict | None
) -> None:
    """Incrementally fold one event's usage into the run's usage_metrics row.

    assistant_message events with a usage block add token counts + 1 iteration;
    tool_use/command_run/file_edit events add 1 tool call; other events are
    ignored. Token columns stay NULL until a usage event contributes.
    """
    if event_type == "assistant_message" and usage:
        conn.execute(
            """
            INSERT INTO usage_metrics (
                run_id, input_tokens, output_tokens, cached_input_tokens,
                iteration_count, cost_source
            )
            VALUES (%(run_id)s, %(inp)s, %(out)s, %(cache)s, 1, 'unavailable')
            ON CONFLICT (run_id) DO UPDATE SET
                input_tokens = COALESCE(usage_metrics.input_tokens, 0)
                             + COALESCE(EXCLUDED.input_tokens, 0),
                output_tokens = COALESCE(usage_metrics.output_tokens, 0)
                              + COALESCE(EXCLUDED.output_tokens, 0),
                cached_input_tokens = COALESCE(usage_metrics.cached_input_tokens, 0)
                                    + COALESCE(EXCLUDED.cached_input_tokens, 0),
                iteration_count = COALESCE(usage_metrics.iteration_count, 0) + 1,
                updated_at = NOW()
            """,
            {
                "run_id": run_id,
                "inp": usage.get("input_tokens"),
                "out": usage.get("output_tokens"),
                "cache": usage.get("cache_read_input_tokens"),
            },
        )
    elif event_type in ("tool_use", "command_run", "file_edit"):
        conn.execute(
            """
            INSERT INTO usage_metrics (run_id, tool_calls_count, cost_source)
            VALUES (%(run_id)s, 1, 'unavailable')
            ON CONFLICT (run_id) DO UPDATE SET
                tool_calls_count = COALESCE(usage_metrics.tool_calls_count, 0) + 1,
                updated_at = NOW()
            """,
            {"run_id": run_id},
        )
```

- [ ] **Step 4: Wire it into the events route**

In `app/routes/events.py:ingest_event`, after the `update_run_time_bounds` block and before `response.status_code = ...`, add:

```python
    if not is_duplicate:
        usage = None
        payload = event.raw_payload
        if isinstance(payload, dict):
            message = payload.get("message")
            if isinstance(message, dict) and isinstance(message.get("usage"), dict):
                usage = message["usage"]
        ingestion.record_event_usage(
            conn, run_id=run_id, event_type=event.event_type, usage=usage
        )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `set -a; . ./.env; set +a; .venv/bin/pytest tests/test_usage_goforward.py tests/test_events.py -v`
Expected: PASS (new go-forward tests + existing event tests; pytest- runs cascade-clean usage_metrics via the FK).

- [ ] **Step 6: Lint and commit**

```bash
.venv/bin/ruff format app tests
.venv/bin/ruff check app tests
git add app/models/ingestion.py app/routes/events.py tests/test_usage_goforward.py
git commit -m "feat: incrementally record per-event token usage on ingest

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Read API — `GET /runs` (list, filter, paginate)

**Files:**
- Create: `app/models/reads.py`
- Create: `app/schemas/reads.py`
- Create: `app/routes/reads.py`
- Modify: `app/main.py`
- Modify: `tests/conftest.py` (add `pytest-` projects cleanup for later overview tests)
- Test: `tests/test_reads_runs.py`

**Interfaces:**
- Produces:
  - `reads.list_runs(conn, *, project_id=None, repository_id=None, status=None, tool_id=None, limit=50, offset=0) -> list[dict]`
  - `RunListItem` schema; `GET /runs` route returning `list[RunListItem]`.
  - `reads.get_run` / `project_overview` and `RunDetail`/`ProjectOverview` are added in Task 4 to the same files.

- [ ] **Step 1: Extend conftest cleanup with pytest- projects**

In `tests/conftest.py`, in the cleanup fixture, add a projects delete AFTER the runs/run_events/repositories deletes (FK order: children before parent):

```python
            conn.execute("DELETE FROM projects WHERE project_id LIKE 'pytest-%'")
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_reads_runs.py
from uuid import uuid4

from app.database import get_connection
from app.models import ingestion

P, R, T = "agentops-core", "agentops-core-main", "claude-code"


def _seed_run(session_id, status="active"):
    with get_connection() as conn:
        run_id, _ = ingestion.get_or_create_run(
            conn, project_id=P, repository_id=R, tool_id=T, session_id=session_id,
            model="claude-opus-4-8", branch="main", cwd="/tmp/work", intent=None,
        )
        if status != "active":
            conn.execute("UPDATE runs SET status = %s WHERE run_id = %s", (status, run_id))
    return str(run_id)


def test_list_runs_returns_seeded_run(client):
    sid = f"pytest-session-{uuid4().hex}"
    run_id = _seed_run(sid)
    resp = client.get("/runs", params={"project_id": P, "limit": 200})
    assert resp.status_code == 200
    ids = {r["run_id"] for r in resp.json()}
    assert run_id in ids


def test_list_runs_filter_by_status(client):
    sid = f"pytest-session-{uuid4().hex}"
    run_id = _seed_run(sid, status="completed")
    resp = client.get("/runs", params={"status": "completed", "limit": 200})
    assert resp.status_code == 200
    rows = resp.json()
    assert run_id in {r["run_id"] for r in rows}
    assert all(r["status"] == "completed" for r in rows)


def test_list_runs_pagination(client):
    resp = client.get("/runs", params={"limit": 2, "offset": 0})
    assert resp.status_code == 200
    assert len(resp.json()) <= 2


def test_list_runs_rejects_bad_limit(client):
    assert client.get("/runs", params={"limit": 9999}).status_code == 422
    assert client.get("/runs", params={"offset": -1}).status_code == 422
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `set -a; . ./.env; set +a; .venv/bin/pytest tests/test_reads_runs.py -v`
Expected: FAIL — `GET /runs` not found (404/405); router not registered.

- [ ] **Step 4: Add the read model**

```python
# app/models/reads.py
"""Read-side queries over run_overview and per-project rollups (raw SQL)."""

from typing import Any

import psycopg

_FILTER_COLUMNS = ("project_id", "repository_id", "status", "tool_id")


def list_runs(
    conn: psycopg.Connection,
    *,
    project_id: str | None = None,
    repository_id: str | None = None,
    status: str | None = None,
    tool_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List runs from run_overview with optional equality filters, newest first."""
    values = {
        "project_id": project_id,
        "repository_id": repository_id,
        "status": status,
        "tool_id": tool_id,
    }
    params: dict[str, Any] = {}
    clauses = []
    for col in _FILTER_COLUMNS:  # column names are a fixed whitelist, not user input
        if values[col] is not None:
            clauses.append(f"{col} = %({col})s")
            params[col] = values[col]
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    params["limit"] = limit
    params["offset"] = offset
    return conn.execute(
        f"SELECT * FROM run_overview{where} "
        "ORDER BY started_at DESC NULLS LAST LIMIT %(limit)s OFFSET %(offset)s",
        params,
    ).fetchall()
```

- [ ] **Step 5: Add the schema**

```python
# app/schemas/reads.py
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class RunListItem(BaseModel):
    model_config = ConfigDict(extra="ignore")  # run_overview has more columns than we expose

    run_id: str
    project_id: str
    project_name: str | None = None
    repository_id: str
    tool_id: str
    model: str | None = None
    session_id: str | None = None
    status: str
    started_at: datetime | None = None
    ended_at: datetime | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    cost_usd: Decimal | None = None
    cost_source: str | None = None
    iteration_count: int | None = None
    tool_calls_count: int | None = None
```

- [ ] **Step 6: Add the route**

```python
# app/routes/reads.py
import psycopg
from fastapi import APIRouter, Depends, Query

from app.database import db_dependency
from app.models import reads
from app.schemas.reads import RunListItem

# NOTE: do not `from fastapi import status` here — the GET /runs `status` query
# param would shadow it. Use literal HTTP codes where needed (Task 4).
router = APIRouter(tags=["reads"])


@router.get("/runs", response_model=list[RunListItem])
def get_runs(
    project_id: str | None = None,
    repository_id: str | None = None,
    status: str | None = None,
    tool_id: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn: psycopg.Connection = Depends(db_dependency),
) -> list[dict]:
    return reads.list_runs(
        conn, project_id=project_id, repository_id=repository_id,
        status=status, tool_id=tool_id, limit=limit, offset=offset,
    )
```

- [ ] **Step 7: Register the router**

In `app/main.py`, add `reads` to the import and include it:

```python
from app.routes import events, health, reads, repositories
```
```python
    app.include_router(reads.router)
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `set -a; . ./.env; set +a; .venv/bin/pytest tests/test_reads_runs.py -v`
Expected: PASS (4 tests).

- [ ] **Step 9: Lint and commit**

```bash
.venv/bin/ruff format app tests
.venv/bin/ruff check app tests
git add app/models/reads.py app/schemas/reads.py app/routes/reads.py app/main.py tests/conftest.py tests/test_reads_runs.py
git commit -m "feat: GET /runs read endpoint (filter + paginate over run_overview)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Read API — `GET /runs/{run_id}` + `GET /overview`

**Files:**
- Modify: `app/models/reads.py`
- Modify: `app/schemas/reads.py`
- Modify: `app/routes/reads.py`
- Test: `tests/test_reads_detail.py`

**Interfaces:**
- Consumes: scaffolding from Task 3 (`reads.py` model/route/schema modules, registered router).
- Produces:
  - `reads.get_run(conn, run_id) -> dict | None`; `reads.project_overview(conn) -> list[dict]`.
  - `RunDetail`, `ProjectOverview` schemas; `GET /runs/{run_id}` (404 on miss) and `GET /overview` routes.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_reads_detail.py
from uuid import uuid4

from app.database import get_connection
from app.models import ingestion

P, R, T = "agentops-core", "agentops-core-main", "claude-code"


def _seed_run_with_usage(session_id, inp, out):
    with get_connection() as conn:
        run_id, _ = ingestion.get_or_create_run(
            conn, project_id=P, repository_id=R, tool_id=T, session_id=session_id,
            model="claude-opus-4-8", branch="main", cwd="/tmp/work", intent=None,
        )
        conn.execute(
            "INSERT INTO usage_metrics (run_id, input_tokens, output_tokens, cost_source) "
            "VALUES (%s, %s, %s, 'unavailable')", (run_id, inp, out),
        )
    return str(run_id)


def test_get_run_returns_row_with_tokens(client):
    sid = f"pytest-session-{uuid4().hex}"
    run_id = _seed_run_with_usage(sid, 123, 45)
    resp = client.get(f"/runs/{run_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == run_id
    assert body["input_tokens"] == 123
    assert body["output_tokens"] == 45


def test_get_run_unknown_is_404(client):
    assert client.get(f"/runs/{uuid4()}").status_code == 404


def test_overview_sums_tokens_for_project(client):
    sid = f"pytest-session-{uuid4().hex}"
    _seed_run_with_usage(sid, 1000, 200)
    resp = client.get("/overview")
    assert resp.status_code == 200
    entry = next(e for e in resp.json() if e["project_id"] == P)
    assert entry["run_count"] >= 1
    assert entry["input_tokens"] is not None and entry["input_tokens"] >= 1000
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `set -a; . ./.env; set +a; .venv/bin/pytest tests/test_reads_detail.py -v`
Expected: FAIL — `GET /runs/{id}` and `GET /overview` not found.

- [ ] **Step 3: Add the model functions**

Append to `app/models/reads.py`:

```python
def get_run(conn: psycopg.Connection, run_id: str) -> dict[str, Any] | None:
    """Return one run_overview row by run_id, or None if not found."""
    return conn.execute(
        "SELECT * FROM run_overview WHERE run_id = %s", (run_id,)
    ).fetchone()


def project_overview(conn: psycopg.Connection) -> list[dict[str, Any]]:
    """Per-project rollup: run count, token totals, and activity time range."""
    return conn.execute(
        """
        SELECT
            p.project_id,
            p.project_name,
            COUNT(r.run_id) AS run_count,
            SUM(u.input_tokens) AS input_tokens,
            SUM(u.output_tokens) AS output_tokens,
            SUM(u.cached_input_tokens) AS cached_input_tokens,
            MIN(r.started_at) AS earliest,
            MAX(COALESCE(r.ended_at, r.started_at)) AS latest_activity
        FROM projects p
        JOIN runs r ON r.project_id = p.project_id
        LEFT JOIN usage_metrics u ON u.run_id = r.run_id
        GROUP BY p.project_id, p.project_name
        ORDER BY run_count DESC
        """
    ).fetchall()
```

- [ ] **Step 4: Add the schemas**

Append to `app/schemas/reads.py`:

```python
class RunDetail(RunListItem):
    branch: str | None = None
    intent: str | None = None
    summary: str | None = None
    human: str | None = None


class ProjectOverview(BaseModel):
    model_config = ConfigDict(extra="ignore")

    project_id: str
    project_name: str | None = None
    run_count: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    earliest: datetime | None = None
    latest_activity: datetime | None = None
```

- [ ] **Step 5: Add the routes**

Append to `app/routes/reads.py` (import `HTTPException`, `RunDetail`, `ProjectOverview`):

```python
@router.get("/runs/{run_id}", response_model=RunDetail)
def get_run(run_id: str, conn: psycopg.Connection = Depends(db_dependency)) -> dict:
    row = reads.get_run(conn, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Unknown run_id: {run_id}")
    return row


@router.get("/overview", response_model=list[ProjectOverview])
def get_overview(conn: psycopg.Connection = Depends(db_dependency)) -> list[dict]:
    return reads.project_overview(conn)
```

Update the import line at the top of `app/routes/reads.py`:
```python
from fastapi import APIRouter, Depends, HTTPException, Query
```
```python
from app.schemas.reads import ProjectOverview, RunDetail, RunListItem
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `set -a; . ./.env; set +a; .venv/bin/pytest tests/test_reads_detail.py tests/test_reads_runs.py -v`
Expected: PASS (detail + overview + the Task 3 list tests still green).

- [ ] **Step 7: Lint and commit**

```bash
.venv/bin/ruff format app tests
.venv/bin/ruff check app tests
git add app/models/reads.py app/schemas/reads.py app/routes/reads.py tests/test_reads_detail.py
git commit -m "feat: GET /runs/{run_id} + GET /overview read endpoints

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Apply usage backfill to real data + document

**Files:**
- Modify: `README.md`

**Interfaces:** none (operational + docs).

- [ ] **Step 1: Rebuild the API container (so the live service has the new routes + go-forward usage)**

Run: `docker compose up -d --build api`
Expected: container recreated and healthy.

- [ ] **Step 2: Backfill usage for existing events**

Run:
```bash
set -a; . ./.env; set +a
.venv/bin/python -m app.maintenance backfill-usage
```
Expected: `usage rows written: <n>` (one row per run that has events; up to 106).

- [ ] **Step 3: Verify usage populated and reads return tokens**

Run:
```bash
set -a; . ./.env; set +a
docker exec agentops-postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc \
  "SELECT 'usage_metrics rows='||count(*) FROM usage_metrics;
   SELECT 'runs with input_tokens='||count(*) FILTER (WHERE input_tokens IS NOT NULL) FROM usage_metrics;"
curl -s "http://localhost:8000/overview" | head -c 600; echo
curl -s "http://localhost:8000/runs?limit=1" | head -c 400; echo
```
Expected: `usage_metrics rows` ≈ 106; most rows have non-NULL `input_tokens`; `/overview` shows per-project token totals; `/runs` returns a run with populated token fields.

- [ ] **Step 4: Confirm idempotency on real data**

Run:
```bash
set -a; . ./.env; set +a
.venv/bin/python -m app.maintenance backfill-usage
docker exec agentops-postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc \
  "SELECT 'sum input_tokens='||COALESCE(SUM(input_tokens),0) FROM usage_metrics;"
```
Expected: total `input_tokens` unchanged from Step 3 (values stable across re-run).

- [ ] **Step 5: Document the read API in the README**

Append a "Read API" section to `README.md`:

```markdown
## Read API

Query captured runs (all read-only JSON):

- `GET /runs` — list runs newest-first. Optional filters `project_id`,
  `repository_id`, `status`, `tool_id`; pagination `limit` (1–200, default 50)
  and `offset`. Each item includes token usage from `usage_metrics`.
- `GET /runs/{run_id}` — one run's detail (404 if unknown).
- `GET /overview` — per-project rollup: run count, token totals, and activity
  time range.

Token usage is derived from captured events into `usage_metrics`
(`cost_usd` is left NULL with `cost_source = 'unavailable'` until an
authoritative cost source is added). To (re)derive usage for stored events:
`python -m app.maintenance backfill-usage`.
```

- [ ] **Step 6: Commit the docs**

```bash
git add README.md
git commit -m "docs: document the read API + backfill-usage command

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**1. Spec coverage:**
- §4 derivation mapping (input/output/cached sums, iteration_count, tool_calls_count over the three tool types, NULL-not-zero) → Task 1 (backfill) + Task 2 (go-forward). ✓
- §5.1 go-forward `record_event_usage` (incremental, not-duplicate guard) + backfill `backfill_usage_metrics` (set-based, idempotent-in-result, cost untouched on conflict) → Tasks 2, 1. ✓
- §5.2 maintenance subcommands (`backfill-run-times`, `backfill-usage`) + README note fix → Task 1. ✓
- §5.3 read models (`list_runs`, `get_run`, `project_overview`) → Tasks 3, 4. ✓
- §5.4 routes + schemas (`GET /runs`, `/runs/{id}` 404, `/overview`; NULL-preserving Optional fields; status-param/`status`-import collision avoided) → Tasks 3, 4. ✓
- §6 data flow / §7 error handling (404, [], 422 via Query bounds, NULL-not-zero) → Tasks 3, 4 tests. ✓
- §8 testing (derivation correctness/NULL/idempotent; endpoint filters/pagination/404; real backfill) → Tasks 1–5. ✓
- §9 scope (no UI, no cost, no SSE, no per-event listing) → not implemented anywhere. ✓

**2. Placeholder scan:** No TBD/TODO; every code step has complete code; commands have concrete expected output; the operational `<n>` is explained. ✓

**3. Type consistency:** `backfill_usage_metrics(conn) -> int`, `record_event_usage(conn, *, run_id: UUID, event_type: str, usage: dict | None) -> None`, `list_runs(conn, *, ...filters..., limit, offset) -> list[dict]`, `get_run(conn, run_id) -> dict | None`, `project_overview(conn) -> list[dict]` are consistent across model, routes, and tests. `RunDetail` extends `RunListItem`; both use `extra="ignore"` so `SELECT *` rows serialize cleanly. `maintenance.main(["backfill-run-times" | "backfill-usage"])` matches the subcommand tests. The route's `status` query param does not import `fastapi.status` (collision avoided; 404 uses a literal). ✓

> Notes: (a) whole-table backfill tests use a single transaction + `conn.rollback()` so they don't persist to shared data (lesson from the run-timestamps milestone); route-driven go-forward tests rely on `pytest-` runs + the `usage_metrics` FK `ON DELETE CASCADE` for cleanup. (b) The backfill's `ON CONFLICT DO UPDATE` deliberately omits `cost_usd`/`cost_source` so a future `reported` cost is never clobbered.
