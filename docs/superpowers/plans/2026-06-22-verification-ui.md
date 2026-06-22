# Verification UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A minimal, read-only browser UI served at `/ui` that renders the AgentOps read API (`/overview`, `/runs`, `/runs/{id}`, and a new `/runs/{id}/events`) so the captured data can be verified by eye.

**Architecture:** Static HTML/JS/CSS files served by the existing FastAPI app via `StaticFiles`, consuming the public JSON API with `fetch` (same-origin). The only backend change is one new read endpoint over `run_events`. No template engine, no build step, no new Python dependencies. Chart.js is loaded from a CDN.

**Tech Stack:** FastAPI + Starlette `StaticFiles`, psycopg 3 raw SQL, Pydantic v2, vanilla JS, Chart.js (CDN), pytest against live Postgres.

## Global Constraints

- Python `>=3.12`; psycopg 3 raw parameterized SQL, no ORM.
- ruff: `line-length = 100`, lint select `E, F, I, UP, B`.
- Tests run against live Postgres; rows prefixed `pytest-`; cleaned up by `tests/conftest.py` (`runs.session_id LIKE 'pytest-%'` cascades to `run_events`; `run_events.source_event_id LIKE 'pytest-%'`).
- Read-only: no endpoint or UI action mutates data.
- **NULL-not-zero stays visible**: token columns and `cost_source`/`cost_usd` are surfaced unmodified from the API. The runs/overview/detail tables render NULL as `—`, never `0`. (The chart is the one display-only exception: a NULL token total plots as a 0-height bar — code-commented as such.)
- New endpoint `GET /runs/{run_id}/events`: unknown `run_id` → `[]` with HTTP 200 (not 404); returns the full `raw_payload`; no pagination.
- No new Python dependencies. The only external/CDN dependency is Chart.js, pinned to an exact version (`chart.js@4.4.6`) and loaded with Subresource Integrity (`integrity="sha384-..."` + `crossorigin="anonymous"`) so a CDN compromise cannot inject altered code. Do not change the version without recomputing the SRI hash.
- UI served at `/ui` from the existing `agentops-api` container.
- The Docker `agentops-api` container serves code built into the image; route/static changes require `docker compose up -d --build api`.
- Commit messages end with: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
- Branch: `feat/verification-ui` (already created, off `feat/read-surface`).

---

## File Structure

- `app/models/reads.py` — **modify**: add `list_run_events(conn, run_id)`.
- `app/schemas/reads.py` — **modify**: add `RunEvent`.
- `app/routes/reads.py` — **modify**: add `GET /runs/{run_id}/events`.
- `app/main.py` — **modify**: mount `StaticFiles` at `/ui`.
- `app/static/index.html` — **create**: overview + charts + runs list/filters shell.
- `app/static/run.html` — **create**: run detail + events shell.
- `app/static/app.js` — **create**: `fetchJSON`, `showError`, `renderTable`, `initIndex`/`loadRuns` (Task 3), `initRun`/`renderDetail`/`renderEvents` (Task 4).
- `app/static/charts.js` — **create**: `renderTokenChart`.
- `app/static/style.css` — **create**: minimal styling.
- `tests/test_reads_events.py` — **create**: events endpoint (Task 1) + static-serve smoke tests (Tasks 2–5).
- `README.md` — **modify**: add "Verification UI" section + manual smoke checklist.

---

## Task 1: Backend endpoint `GET /runs/{run_id}/events`

**Files:**
- Modify: `app/models/reads.py`
- Modify: `app/schemas/reads.py`
- Modify: `app/routes/reads.py`
- Test: `tests/test_reads_events.py`

**Interfaces:**
- Consumes: `app.models.ingestion.get_or_create_run(conn, *, project_id, repository_id, tool_id, session_id, model, branch, cwd, intent) -> (UUID, bool)`; `app.models.ingestion.insert_run_event(conn, *, source_event_id, run_id, tool_id, session_id, event_type, files_touched, raw_payload, redaction_status, occurred_at) -> (UUID, bool)`.
- Produces: `app.models.reads.list_run_events(conn, run_id: str) -> list[dict]`; `app.schemas.reads.RunEvent`; route `GET /runs/{run_id}/events -> list[RunEvent]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_reads_events.py`:

```python
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.database import get_connection
from app.models import ingestion

P, R, T = "agentops-core", "agentops-core-main", "claude-code"


def _seed_run_with_events(session_id):
    """Create a run with two events inserted OUT of time order."""
    with get_connection() as conn:
        run_id, _ = ingestion.get_or_create_run(
            conn,
            project_id=P,
            repository_id=R,
            tool_id=T,
            session_id=session_id,
            model="claude-opus-4-8",
            branch="main",
            cwd="/tmp/work",
            intent=None,
        )
        base = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        # Insert the LATER event first to prove ORDER BY occurred_at ASC.
        ingestion.insert_run_event(
            conn,
            source_event_id=f"pytest-{uuid4().hex}",
            run_id=run_id,
            tool_id=T,
            session_id=session_id,
            event_type="assistant_message",
            files_touched=[],
            raw_payload={"seq": 2},
            redaction_status="clean",
            occurred_at=base + timedelta(minutes=5),
        )
        ingestion.insert_run_event(
            conn,
            source_event_id=f"pytest-{uuid4().hex}",
            run_id=run_id,
            tool_id=T,
            session_id=session_id,
            event_type="tool_use",
            files_touched=["a.py"],
            raw_payload={"seq": 1, "nested": {"k": "v"}},
            redaction_status="redacted",
            occurred_at=base,
        )
    return str(run_id)


def test_events_returned_oldest_first(client):
    sid = f"pytest-session-{uuid4().hex}"
    run_id = _seed_run_with_events(sid)
    resp = client.get(f"/runs/{run_id}/events")
    assert resp.status_code == 200
    body = resp.json()
    assert [e["raw_payload"]["seq"] for e in body] == [1, 2]


def test_events_unknown_run_is_empty_list(client):
    resp = client.get(f"/runs/{uuid4()}/events")
    assert resp.status_code == 200
    assert resp.json() == []


def test_events_full_raw_payload_roundtrips(client):
    sid = f"pytest-session-{uuid4().hex}"
    run_id = _seed_run_with_events(sid)
    body = client.get(f"/runs/{run_id}/events").json()
    first = body[0]
    assert first["raw_payload"] == {"seq": 1, "nested": {"k": "v"}}


def test_events_expose_redaction_and_files(client):
    sid = f"pytest-session-{uuid4().hex}"
    run_id = _seed_run_with_events(sid)
    body = client.get(f"/runs/{run_id}/events").json()
    first = body[0]
    assert first["redaction_status"] == "redacted"
    assert first["files_touched"] == ["a.py"]
    assert first["event_type"] == "tool_use"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `set -a; . ./.env; set +a; .venv/bin/python -m pytest tests/test_reads_events.py -v`
Expected: FAIL — the route `GET /runs/{run_id}/events` does not exist yet (404, so `resp.json()` assertions fail / status mismatch).

- [ ] **Step 3: Add the model query**

Append to `app/models/reads.py` (after `project_overview`):

```python
def list_run_events(conn: psycopg.Connection, run_id: str) -> list[dict[str, Any]]:
    """Return one run's events oldest-first; empty list if the run is unknown."""
    return conn.execute(
        """
        SELECT event_id, event_type, tool_name, files_touched,
               redaction_status, occurred_at, received_at, raw_payload
        FROM run_events
        WHERE run_id = %s
        ORDER BY occurred_at ASC NULLS LAST, received_at ASC
        """,
        (run_id,),
    ).fetchall()
```

- [ ] **Step 4: Add the response schema**

Append to `app/schemas/reads.py` (after `ProjectOverview`):

```python
class RunEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    event_id: UUID
    event_type: str
    tool_name: str | None = None
    files_touched: list[str]
    redaction_status: str
    occurred_at: datetime | None = None
    received_at: datetime | None = None
    raw_payload: dict
```

(`UUID`, `datetime`, `ConfigDict`, `BaseModel` are already imported in this file.)

- [ ] **Step 5: Add the route**

In `app/routes/reads.py`, update the schema import line and add the route after `get_run`:

Change the import:
```python
from app.schemas.reads import ProjectOverview, RunDetail, RunEvent, RunListItem
```

Add after the `get_run` function (before `get_overview`):
```python
@router.get("/runs/{run_id}/events", response_model=list[RunEvent])
def get_run_events(
    run_id: str, conn: psycopg.Connection = Depends(db_dependency)
) -> list[dict]:
    return reads.list_run_events(conn, run_id)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `set -a; . ./.env; set +a; .venv/bin/python -m pytest tests/test_reads_events.py -v`
Expected: PASS — 4 passed.

- [ ] **Step 7: Lint**

Run: `.venv/bin/ruff check app tests`
Expected: `All checks passed!`

- [ ] **Step 8: Commit**

```bash
git add app/models/reads.py app/schemas/reads.py app/routes/reads.py tests/test_reads_events.py
git commit -m "feat: GET /runs/{run_id}/events read endpoint

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Mount the static UI at `/ui`

**Files:**
- Modify: `app/main.py`
- Create: `app/static/index.html`
- Test: `tests/test_reads_events.py` (append)

**Interfaces:**
- Consumes: the FastAPI `app` in `app/main.py`.
- Produces: a `StaticFiles` mount at `/ui` serving `app/static/`; `GET /ui/` returns `index.html`.

- [ ] **Step 1: Write the failing smoke test**

Append to `tests/test_reads_events.py`:

```python
def test_ui_index_is_served(client):
    resp = client.get("/ui/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "AgentOps" in resp.text
```

- [ ] **Step 2: Run it to verify it fails**

Run: `set -a; . ./.env; set +a; .venv/bin/python -m pytest tests/test_reads_events.py::test_ui_index_is_served -v`
Expected: FAIL — 404, no `/ui` mount yet.

- [ ] **Step 3: Create the static directory and index shell**

Create `app/static/index.html`:

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>AgentOps — Verification UI</title>
  <link rel="stylesheet" href="style.css" />
</head>
<body>
  <h1>AgentOps — Verification UI</h1>
  <p class="note">Read-only verification surface over the AgentOps read API.</p>

  <section>
    <h2>Overview</h2>
    <canvas id="token-chart" height="120"></canvas>
    <div id="overview">Loading…</div>
  </section>

  <section>
    <h2>Runs</h2>
    <form id="filter-form">
      <input id="project_id" placeholder="project_id" />
      <input id="repository_id" placeholder="repository_id" />
      <input id="status" placeholder="status" />
      <input id="tool_id" placeholder="tool_id" />
      <button type="submit">Filter</button>
    </form>
    <div class="pager">
      <button id="prev" type="button">Prev</button>
      <span id="page-info"></span>
      <button id="next" type="button">Next</button>
    </div>
    <div id="runs">Loading…</div>
  </section>

  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.6/dist/chart.umd.min.js"
          integrity="sha384-Sse/HDqcypGpyTDpvZOJNnG0TT3feGQUkF9H+mnRvic+LjR+K1NhTt8f51KIQ3v3"
          crossorigin="anonymous"></script>
  <script src="charts.js"></script>
  <script src="app.js"></script>
  <script>initIndex();</script>
</body>
</html>
```

(`charts.js` and `app.js` are created in later tasks; until then the page serves but its scripts 404 — acceptable between tasks. The smoke test only checks the served HTML.)

- [ ] **Step 4: Mount StaticFiles in `app/main.py`**

Add the import near the other imports:
```python
from pathlib import Path

from fastapi.staticfiles import StaticFiles
```

At the end of `create_app()`, after `app.include_router(reads.router)` and before `return app`:
```python
    # Minimal read-only verification UI (static files consuming the JSON API).
    # Path is resolved relative to this package so it works both from source
    # (tests) and from the installed wheel (container).
    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/ui", StaticFiles(directory=static_dir, html=True), name="ui")
```

- [ ] **Step 5: Run the smoke test to verify it passes**

Run: `set -a; . ./.env; set +a; .venv/bin/python -m pytest tests/test_reads_events.py::test_ui_index_is_served -v`
Expected: PASS.

- [ ] **Step 6: Lint**

Run: `.venv/bin/ruff check app tests`
Expected: `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add app/main.py app/static/index.html tests/test_reads_events.py
git commit -m "feat: mount minimal verification UI at /ui

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Overview + runs list/filters (`app.js` + `style.css`)

**Files:**
- Create: `app/static/app.js`
- Create: `app/static/style.css`
- Test: `tests/test_reads_events.py` (append)

**Interfaces:**
- Consumes: `GET /overview`, `GET /runs?...`; DOM ids in `index.html` (`overview`, `runs`, `filter-form`, `project_id`, `repository_id`, `status`, `tool_id`, `prev`, `next`, `page-info`, `token-chart`).
- Produces: global JS functions `fetchJSON(url)`, `showError(el, err)`, `renderTable(container, rows, columns, link)`, `initIndex()`, `loadRuns()`; optional hook to `window.renderTokenChart` (defined in Task 5).

- [ ] **Step 1: Write the failing static-serve test**

Append to `tests/test_reads_events.py`:

```python
def test_ui_app_js_is_served(client):
    resp = client.get("/ui/app.js")
    assert resp.status_code == 200
    assert "javascript" in resp.headers["content-type"]
    assert "function initIndex" in resp.text
```

- [ ] **Step 2: Run it to verify it fails**

Run: `set -a; . ./.env; set +a; .venv/bin/python -m pytest tests/test_reads_events.py::test_ui_app_js_is_served -v`
Expected: FAIL — 404, `app.js` does not exist.

- [ ] **Step 3: Create `app/static/app.js`**

```javascript
// Shared helpers + index-page logic for the verification UI.
// Pure read-only: every call is a GET against the JSON API, same origin.

async function fetchJSON(url) {
  const resp = await fetch(url);
  if (!resp.ok) {
    const body = await resp.text();
    throw new Error(`${resp.status} ${resp.statusText}: ${body}`);
  }
  return resp.json();
}

function showError(el, err) {
  // A verification tool must SHOW failures, never blank them out.
  el.innerHTML = "";
  const box = document.createElement("div");
  box.className = "error";
  box.textContent = `Request failed — ${err.message}`;
  el.appendChild(box);
}

function cell(value) {
  // NULL stays visible as an em-dash; never coerced to 0.
  return value === null || value === undefined ? "—" : String(value);
}

function renderTable(container, rows, columns, link) {
  container.innerHTML = "";
  const table = document.createElement("table");
  const headRow = table.createTHead().insertRow();
  for (const col of columns) {
    const th = document.createElement("th");
    th.textContent = col.label;
    headRow.appendChild(th);
  }
  const body = table.createTBody();
  for (const row of rows) {
    const tr = body.insertRow();
    for (const col of columns) {
      const td = tr.insertCell();
      const text = cell(row[col.key]);
      if (link && col.key === link.key) {
        const a = document.createElement("a");
        a.href = link.href(row);
        a.textContent = text;
        td.appendChild(a);
      } else {
        // textContent (never innerHTML): captured data is untrusted.
        td.textContent = text;
      }
    }
  }
  container.appendChild(table);
}

const OVERVIEW_COLUMNS = [
  { key: "project_id", label: "Project" },
  { key: "project_name", label: "Name" },
  { key: "run_count", label: "Runs" },
  { key: "input_tokens", label: "Input" },
  { key: "output_tokens", label: "Output" },
  { key: "cached_input_tokens", label: "Cached" },
  { key: "earliest", label: "Earliest" },
  { key: "latest_activity", label: "Latest" },
];

const RUN_COLUMNS = [
  { key: "run_id", label: "Run" },
  { key: "project_id", label: "Project" },
  { key: "repository_id", label: "Repo" },
  { key: "tool_id", label: "Tool" },
  { key: "status", label: "Status" },
  { key: "started_at", label: "Started" },
  { key: "input_tokens", label: "Input" },
  { key: "output_tokens", label: "Output" },
  { key: "cached_input_tokens", label: "Cached" },
  { key: "cost_source", label: "Cost src" },
];

const state = { limit: 50, offset: 0 };

async function loadRuns() {
  const el = document.getElementById("runs");
  const params = new URLSearchParams();
  for (const f of ["project_id", "repository_id", "status", "tool_id"]) {
    const v = document.getElementById(f).value.trim();
    if (v) params.set(f, v);
  }
  params.set("limit", state.limit);
  params.set("offset", state.offset);
  document.getElementById("page-info").textContent = `offset ${state.offset}`;
  try {
    const runs = await fetchJSON(`/runs?${params.toString()}`);
    if (runs.length === 0) {
      el.innerHTML = "";
      const p = document.createElement("p");
      p.textContent = "No runs match.";
      el.appendChild(p);
      return;
    }
    renderTable(el, runs, RUN_COLUMNS, {
      key: "run_id",
      href: (r) => `run.html?id=${encodeURIComponent(r.run_id)}`,
    });
  } catch (err) {
    showError(el, err);
  }
}

async function initIndex() {
  const overviewEl = document.getElementById("overview");
  try {
    const overview = await fetchJSON("/overview");
    renderTable(overviewEl, overview, OVERVIEW_COLUMNS);
    if (window.renderTokenChart) window.renderTokenChart(overview);
  } catch (err) {
    showError(overviewEl, err);
  }

  await loadRuns();

  document.getElementById("filter-form").addEventListener("submit", (e) => {
    e.preventDefault();
    state.offset = 0;
    loadRuns();
  });
  document.getElementById("prev").addEventListener("click", () => {
    state.offset = Math.max(0, state.offset - state.limit);
    loadRuns();
  });
  document.getElementById("next").addEventListener("click", () => {
    state.offset += state.limit;
    loadRuns();
  });
}
```

- [ ] **Step 4: Create `app/static/style.css`**

```css
body { font-family: system-ui, sans-serif; margin: 2rem; color: #1a1a1a; }
h1 { font-size: 1.4rem; }
h2 { font-size: 1.1rem; margin-top: 2rem; }
.note { color: #666; }
table { border-collapse: collapse; width: 100%; font-size: 0.85rem; }
th, td { border: 1px solid #ddd; padding: 4px 8px; text-align: left; }
th { background: #f5f5f5; }
form input { margin-right: 6px; padding: 4px; }
.pager { margin: 8px 0; }
.pager button { margin-right: 6px; }
.error { background: #fee; border: 1px solid #c00; color: #900; padding: 8px; }
.event { border: 1px solid #ddd; margin: 6px 0; padding: 6px; }
.event-head { font-family: monospace; font-size: 0.8rem; }
pre { background: #f8f8f8; padding: 8px; overflow-x: auto; }
canvas { max-width: 640px; }
dl { display: grid; grid-template-columns: max-content 1fr; gap: 2px 12px; }
dt { font-weight: 600; }
dd { margin: 0; font-family: monospace; }
```

- [ ] **Step 5: Run the static-serve test to verify it passes**

Run: `set -a; . ./.env; set +a; .venv/bin/python -m pytest tests/test_reads_events.py::test_ui_app_js_is_served -v`
Expected: PASS.

- [ ] **Step 6: Manual smoke check**

Run the app locally and load the page:
Run: `set -a; . ./.env; set +a; .venv/bin/uvicorn app.main:app --port 8000 &` then open `http://localhost:8000/ui/` in a browser.
Expected: the Overview table shows projects; the Runs table shows runs newest-first; typing a known `project_id` and clicking Filter narrows the list; Prev/Next change the offset. (Charts are absent until Task 5 — that is expected.)
Stop the server afterward: `kill %1`.

- [ ] **Step 7: Commit**

```bash
git add app/static/app.js app/static/style.css tests/test_reads_events.py
git commit -m "feat: verification UI overview + runs list with filters

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Run detail + events view (`run.html` + `app.js`)

**Files:**
- Create: `app/static/run.html`
- Modify: `app/static/app.js` (append `initRun`, `renderDetail`, `renderEvents`)
- Test: `tests/test_reads_events.py` (append)

**Interfaces:**
- Consumes: `GET /runs/{id}`, `GET /runs/{id}/events`; the `?id=` query param; DOM ids `detail`, `events`; helpers `fetchJSON`, `showError`, `cell` from Task 3.
- Produces: global JS functions `initRun()`, `renderDetail(container, run)`, `renderEvents(container, events)`; page `run.html`.

- [ ] **Step 1: Write the failing static-serve test**

Append to `tests/test_reads_events.py`:

```python
def test_ui_run_html_is_served(client):
    resp = client.get("/ui/run.html")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "initRun" in resp.text
```

- [ ] **Step 2: Run it to verify it fails**

Run: `set -a; . ./.env; set +a; .venv/bin/python -m pytest tests/test_reads_events.py::test_ui_run_html_is_served -v`
Expected: FAIL — 404, `run.html` does not exist.

- [ ] **Step 3: Create `app/static/run.html`**

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>AgentOps — Run detail</title>
  <link rel="stylesheet" href="style.css" />
</head>
<body>
  <p><a href="index.html">← all runs</a></p>
  <h1>Run detail</h1>
  <section><div id="detail">Loading…</div></section>
  <section>
    <h2>Events</h2>
    <div id="events">Loading…</div>
  </section>
  <script src="app.js"></script>
  <script>initRun();</script>
</body>
</html>
```

- [ ] **Step 4: Append detail/events logic to `app/static/app.js`**

Append at the end of `app/static/app.js`:

```javascript
const DETAIL_FIELDS = [
  "run_id", "project_id", "repository_id", "tool_id", "model", "status",
  "branch", "intent", "summary", "human", "session_id",
  "started_at", "ended_at",
  "input_tokens", "output_tokens", "cached_input_tokens",
  "cost_usd", "cost_source", "iteration_count", "tool_calls_count",
];

function renderDetail(container, run) {
  container.innerHTML = "";
  const dl = document.createElement("dl");
  for (const field of DETAIL_FIELDS) {
    const dt = document.createElement("dt");
    dt.textContent = field;
    const dd = document.createElement("dd");
    dd.textContent = cell(run[field]);
    dl.appendChild(dt);
    dl.appendChild(dd);
  }
  container.appendChild(dl);
}

function renderEvents(container, events) {
  container.innerHTML = "";
  if (events.length === 0) {
    const p = document.createElement("p");
    p.textContent = "No events.";
    container.appendChild(p);
    return;
  }
  for (const ev of events) {
    const card = document.createElement("div");
    card.className = "event";

    const head = document.createElement("div");
    head.className = "event-head";
    const files = (ev.files_touched || []).join(", ");
    head.textContent =
      `${cell(ev.occurred_at)}  ${ev.event_type}  ${cell(ev.tool_name)}  ` +
      `[${ev.redaction_status}]  ${files}`;

    const details = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = "raw_payload";
    const pre = document.createElement("pre");
    try {
      pre.textContent = JSON.stringify(ev.raw_payload, null, 2);
    } catch (e) {
      // Defensive: never let one bad payload break the whole page.
      pre.textContent = String(ev.raw_payload);
    }
    details.appendChild(summary);
    details.appendChild(pre);

    card.appendChild(head);
    card.appendChild(details);
    container.appendChild(card);
  }
}

async function initRun() {
  const id = new URLSearchParams(window.location.search).get("id");
  const detailEl = document.getElementById("detail");
  const eventsEl = document.getElementById("events");
  if (!id) {
    showError(detailEl, new Error("missing ?id= in URL"));
    return;
  }
  try {
    const run = await fetchJSON(`/runs/${encodeURIComponent(id)}`);
    renderDetail(detailEl, run);
  } catch (err) {
    showError(detailEl, err);
  }
  try {
    const events = await fetchJSON(`/runs/${encodeURIComponent(id)}/events`);
    renderEvents(eventsEl, events);
  } catch (err) {
    showError(eventsEl, err);
  }
}
```

- [ ] **Step 5: Run the static-serve test to verify it passes**

Run: `set -a; . ./.env; set +a; .venv/bin/python -m pytest tests/test_reads_events.py::test_ui_run_html_is_served -v`
Expected: PASS.

- [ ] **Step 6: Manual smoke check**

Run: `set -a; . ./.env; set +a; .venv/bin/uvicorn app.main:app --port 8000 &` then open `http://localhost:8000/ui/`, click a run's id link.
Expected: `run.html` shows the detail field list (NULLs render as `—`), then an events list; each event has a collapsible `raw_payload` and shows its `redaction_status`. Opening `run.html` with no `?id=` shows a visible error box.
Stop the server afterward: `kill %1`.

- [ ] **Step 7: Commit**

```bash
git add app/static/run.html app/static/app.js tests/test_reads_events.py
git commit -m "feat: verification UI run detail + events drill-down

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Token chart (`charts.js`)

**Files:**
- Create: `app/static/charts.js`
- Test: `tests/test_reads_events.py` (append)

**Interfaces:**
- Consumes: the `Chart` global (Chart.js, loaded from CDN in `index.html`); the `#token-chart` canvas; the `/overview` array passed by `initIndex` (Task 3).
- Produces: `window.renderTokenChart(overview)`.

- [ ] **Step 1: Write the failing static-serve test**

Append to `tests/test_reads_events.py`:

```python
def test_ui_charts_js_is_served(client):
    resp = client.get("/ui/charts.js")
    assert resp.status_code == 200
    assert "javascript" in resp.headers["content-type"]
    assert "renderTokenChart" in resp.text
```

- [ ] **Step 2: Run it to verify it fails**

Run: `set -a; . ./.env; set +a; .venv/bin/python -m pytest tests/test_reads_events.py::test_ui_charts_js_is_served -v`
Expected: FAIL — 404, `charts.js` does not exist.

- [ ] **Step 3: Create `app/static/charts.js`**

```javascript
// Per-project token bar chart, drawn with Chart.js (loaded from CDN).
// Display-only: a NULL token total plots as a 0-height bar. This does not
// alter the underlying data (the tables still show NULL as "—").
window.renderTokenChart = function renderTokenChart(overview) {
  const canvas = document.getElementById("token-chart");
  if (!canvas || typeof Chart === "undefined") return;
  const labels = overview.map((p) => p.project_id);
  const input = overview.map((p) => p.input_tokens || 0);
  const output = overview.map((p) => p.output_tokens || 0);
  new Chart(canvas, {
    type: "bar",
    data: {
      labels,
      datasets: [
        { label: "Input tokens", data: input },
        { label: "Output tokens", data: output },
      ],
    },
    options: { responsive: true, scales: { y: { beginAtZero: true } } },
  });
};
```

- [ ] **Step 4: Run the static-serve test to verify it passes**

Run: `set -a; . ./.env; set +a; .venv/bin/python -m pytest tests/test_reads_events.py::test_ui_charts_js_is_served -v`
Expected: PASS.

- [ ] **Step 5: Manual smoke check**

Run: `set -a; . ./.env; set +a; .venv/bin/uvicorn app.main:app --port 8000 &` then open `http://localhost:8000/ui/`.
Expected: a bar chart above the Overview table with one input/output pair of bars per project, whose heights match the token totals in the table below.
Stop the server afterward: `kill %1`.

- [ ] **Step 6: Commit**

```bash
git add app/static/charts.js tests/test_reads_events.py
git commit -m "feat: verification UI per-project token chart

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: Docs — README "Verification UI" section + container verify

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: everything above.
- Produces: documentation; a verified container build serving `/ui`.

- [ ] **Step 1: Run the full suite green**

Run: `set -a; . ./.env; set +a; .venv/bin/python -m pytest -q`
Expected: all tests pass (the prior suite plus the new `tests/test_reads_events.py`).

- [ ] **Step 2: Rebuild the container and verify `/ui` is served from the image**

This confirms the static files were bundled into the wheel (hatchling includes them by default, but verify rather than assume).
Run:
```bash
docker compose up -d --build api
sleep 5
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/ui/
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/ui/app.js
```
Expected: `200` for both. If `/ui/` is 404 or the API fails to start (missing static dir), the static files were not packaged — add to `pyproject.toml`:
```toml
[tool.hatch.build.targets.wheel.force-include]
"app/static" = "app/static"
```
then rebuild and re-verify.

- [ ] **Step 3: Add the README section**

Append to `README.md` (after the `## Read API` section):

```markdown
## Verification UI

A minimal, read-only browser UI for eyeballing captured data. It is a
verification surface, not the product UI — it consumes the JSON read API
exactly as any client would.

Once the container is running, open <http://localhost:8000/ui/>:

- **Overview** — per-project run counts and token totals, with a token bar
  chart.
- **Runs** — every run, newest-first, with the same filters and pagination as
  `GET /runs`. Click a run to open its detail.
- **Run detail** — all run fields plus a drill-down into the run's normalized
  events (`GET /runs/{run_id}/events`), each with its `redaction_status` and a
  collapsible `raw_payload`.

NULL token/cost values render as `—` (never `0`), so the
missing-stays-unavailable rule is visible. Chart.js is loaded from a CDN; no
other frontend dependency is added.

### Manual smoke checklist

1. Open `/ui` — the real runs render in the Runs table.
2. Filter by a known `project_id` — the table narrows.
3. Open a run — detail header and events list render.
4. An event shows a collapsed `raw_payload` and a `redaction_status`.
5. The per-project token chart matches the numbers in the Overview table.
```

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: document the verification UI + manual smoke checklist

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Notes for the implementer

- **Branch base:** `feat/verification-ui` is branched off `feat/read-surface` (PR #5, not yet merged) because the UI depends on the read API. When PR #5 merges, this branch folds in cleanly.
- **No new Python deps.** `StaticFiles` ships with Starlette. Do not add a template engine or any JS toolchain.
- **Untrusted data:** captured `raw_payload` and field values are rendered with `textContent` / `JSON.stringify`, never `innerHTML`. Keep it that way.
- **Read-only:** no task adds a write path. If you find yourself adding a POST/PUT/DELETE, stop — it is out of scope.
