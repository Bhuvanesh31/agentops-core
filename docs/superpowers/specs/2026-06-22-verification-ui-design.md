# Verification UI — Design Spec

**Date:** 2026-06-22
**Status:** Approved (brainstorm) — ready for implementation plan
**Branch (proposed):** `feat/verification-ui`
**Depends on:** the read surface (`GET /runs`, `GET /runs/{run_id}`, `GET /overview`) from PR #5.

## Purpose

A small, deliberately minimal visual layer to **verify, by eye, that the data
pipeline built so far actually works** — capture → store → token derivation →
read API. It is a testing/verification surface, **not** the product's final UI.

This is consistent with `AGENTS.md` ("Do not build the **full** user interface,
evaluator, or recommendation engine yet"): the verification UI is a thin,
read-only window onto the existing read API, with no evaluator and no
recommendation engine. It exists so we can confirm the foundation is correct
before building more on top of it. When the real UI is built later, this layer
is disposable and can be removed cleanly because it is fully decoupled from
backend internals (it consumes only the public JSON API).

## Scope

In scope (all three verification tiers):

1. Render the three existing read endpoints: `/overview`, `/runs` (with its
   filters + pagination), `/runs/{run_id}`.
2. Drill into a run's raw normalized events — requires **one new read
   endpoint**, `GET /runs/{run_id}/events`.
3. Simple token charts (per-project token totals).

Out of scope: authentication (localhost, single-user, self-hosted); write
actions of any kind; evaluator / recommendation / analytics engines; a build
toolchain or Node/npm; automated browser tests; mobile/responsive polish.

## Architecture

A static frontend served by the existing FastAPI app (`agentops-api`
container). The only backend change is one new read endpoint. The frontend is
plain static files — no template engine, no build step, same-origin (so no
CORS), Chart.js loaded from CDN.

The UI consumes the public JSON API exactly as any client would. This is
deliberate: it makes the UI a genuine end-to-end test of the read surface,
and keeps it decoupled from `app/models` internals.

### File layout

```
app/
  routes/reads.py      # + GET /runs/{run_id}/events
  models/reads.py      # + list_run_events(conn, run_id)
  schemas/reads.py     # + RunEvent
  main.py              # + mount StaticFiles at /ui
app/static/            # NEW — the entire frontend
  index.html           # overview + charts + runs list/filters
  run.html             # run detail + its events
  app.js               # fetch() + table/render/filter logic
  charts.js            # Chart.js setup
  style.css            # minimal styling
tests/
  test_reads_events.py # NEW — events endpoint + static-mount smoke
```

No new Python dependencies: `StaticFiles` ships with Starlette/FastAPI.

## Backend: new endpoint

`GET /runs/{run_id}/events` — one run's normalized events, oldest-first.
Mirrors the existing `get_run` / `RunDetail` pattern.

**`list_run_events(conn, run_id) -> list[dict]`** in `app/models/reads.py`:

```sql
SELECT event_id, event_type, tool_name, files_touched,
       redaction_status, occurred_at, received_at, raw_payload
FROM run_events
WHERE run_id = %s
ORDER BY occurred_at ASC NULLS LAST, received_at ASC
```

Uses the existing `idx_run_events_run` index. `run_id` is bound as a parameter.

**`RunEvent`** schema in `app/schemas/reads.py`, `ConfigDict(extra="ignore")`:

- `event_id: UUID`
- `event_type: str`
- `tool_name: str | None`
- `files_touched: list[str]`
- `redaction_status: str`
- `occurred_at: datetime | None`
- `received_at: datetime | None`
- `raw_payload: dict`

**Route** in `app/routes/reads.py`: `GET /runs/{run_id}/events` returns
`list[RunEvent]`.

### Endpoint design decisions

1. **Unknown `run_id` → `[]` with 200**, not 404. The UI only calls this after
   loading a known run, and avoiding an existence check keeps it a single
   query. (This differs from `GET /runs/{run_id}`, which returns 404 — that is
   intentional and noted.)
2. **Full `raw_payload` returned** per event (already redacted server-side at
   ingestion). This is what makes the events view a real "is the captured data
   correct?" check. The UI shows it collapsed.
3. **No pagination** — runs hold tens to low-hundreds of events; acceptable for
   a verification tool. If a run ever has thousands, this is revisited.

## Frontend: views & data flow

Two pages, plain `fetch()` against relative (same-origin) URLs. The UI holds no
state beyond the current URL's query params; reload re-fetches. This
statelessness is deliberate — it keeps the UI a transparent window onto the API.

### `index.html` — landing / verification page

- **Overview panel:** `GET /overview` → table of projects (run count, token
  totals, activity range). Below it, **charts** (`charts.js`): bar chart of
  input/output tokens per project.
- **Runs panel:** filter controls (`project_id`, `repository_id`, `status`,
  `tool_id`) + `limit` / `offset` → `GET /runs?...` → table, newest-first.
  Each row links to `run.html?id=<run_id>`. Next/prev controls drive `offset`.
  Token columns and `cost_source` are shown so NULL-not-zero behavior is
  visible by eye.

### `run.html` — run detail + events

- Reads `?id=` from the URL, then fires two fetches: `GET /runs/{id}` (detail,
  incl. branch / intent / summary / human) and `GET /runs/{id}/events`.
- Renders the detail header, then an events table: `occurred_at`,
  `event_type`, `tool_name`, `redaction_status`, `files_touched`, and a
  collapsible `raw_payload` rendered as pretty JSON.

### Data flow

browser → `fetch` relative URL → FastAPI JSON route → `app/models` → Postgres.

## Error handling

A verification tool must **show** failures, never hide them (AGENTS.md: "Failed
ingestion is visible").

- **Backend:** the new endpoint adds no new failure modes — DB errors propagate
  through the existing `db_dependency` (rolls back; FastAPI returns 500).
  Unknown `run_id` is `[]` by design, not an error.
- **Frontend fetch failures:** every `fetch` is wrapped; on non-2xx or network
  error, the panel renders a visible inline error box with the status code and
  response body (e.g. a `422` from a bad `limit`). No silent catch-and-blank —
  a blank table would lie about whether the API works.
- **Empty vs. error are distinct:** an empty result renders an explicit
  "No runs match" / "No events" message, so "API works, nothing matches" is
  visibly different from "API failed."
- **Malformed / huge `raw_payload`:** rendered defensively — if JSON
  pretty-print throws, fall back to showing the raw string rather than breaking
  the page.

## Testing

Scaled to a verification tool: real automated coverage where it has value,
honest manual steps where it does not.

### Backend (TDD, pytest, live Postgres)

`tests/test_reads_events.py`, mirroring `test_reads_detail.py` (pytest-prefixed
seed rows, cleanup):

1. Events returned **oldest-first** (`occurred_at ASC NULLS LAST`).
2. Unknown `run_id` → **`[]`** with 200.
3. **Full `raw_payload`** round-trips — a known JSON body comes back intact.
4. `redaction_status` and `files_touched` present and correctly typed.

### Static-mount smoke test

One pytest via `TestClient` asserting `GET /ui/` returns 200 and serves
`index.html` — proves the mount is wired.

### Frontend

No automated UI tests — a browser-driven suite (Playwright/Node) would
contradict the minimal, no-Node intent. Instead, a documented **manual smoke
checklist**:

1. Open `/ui` → the real runs render in the runs table.
2. Filter by a known project → the table narrows correctly.
3. Open a run → detail header + events table render.
4. A run's event shows a collapsed `raw_payload` and a `redaction_status`.
5. The per-project token chart matches the numbers in the `/overview` table.

The UI is the manual test; the spec makes that explicit rather than pretending
otherwise.

## Invariants preserved

- Read-only: no endpoint or UI action mutates data.
- NULL-not-zero remains visible: token columns and `cost_source` are surfaced
  unmodified from the API (no client-side zero-filling).
- Redaction visibility: `redaction_status` is surfaced per event, so the UI
  doubles as a check that redaction ran.
- No new Python dependencies; no build toolchain.

## Build order (for the plan)

1. New backend endpoint: `list_run_events` + `RunEvent` + route (TDD).
2. Static mount (`/ui`) + smoke test + skeleton `index.html`.
3. Overview panel + runs list/filters (`app.js`).
4. Run detail + events view (`run.html`).
5. Charts (`charts.js`).
6. Error boxes + empty-state messaging pass.
7. README "Verification UI" section + manual smoke checklist.
