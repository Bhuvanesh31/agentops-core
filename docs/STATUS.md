# AgentOps Core — Project Status

**As of:** 2026-06-22  
**Branch:** `main` @ `e9751c0`  
**GitHub:** https://github.com/Bhuvanesh31/agentops-core  
**Stack:** FastAPI + psycopg3 + PostgreSQL · Docker Compose · Python 3.11 · Hatchling

---

## 1. North-Star Vision

A self-hosted **AI-agent observability + registry platform** — the goal is to be *stronger and better* than observal.io (BlazeUp-AI/Observal), not to clone it.

Target capability arc (in order):

1. Session-data capture ✅ **DONE**
2. Real-time agent session overview ✅ **DONE (read API + verification UI)**
3. Token-spend visibility ✅ **DONE (usage_metrics, bar chart)**
4. AI-investment strategy (cross-model cost) — *partially: cost_usd stays NULL until OTEL*
5. Agent registry — *not started*
6. Observability / tracing — *not started*
7. Agent insights / evals — *not started*
8. Human-in-the-loop development — *not started*

Differentiation wedges (where to beat observal.io):
- **AI Work Journal** — narrative / human layer on top of captured runs
- **Git/PR-reconciled shipped-outcome metrics** — cost-per-feature, time-to-merge
- **Claude Code vs Codex comparison** — cross-tool benchmarking

---

## 2. Current Setup

### Repository layout

```
agentops_core/
├── app/                        # FastAPI application (installed as wheel)
│   ├── main.py                 # App factory; mounts /ui StaticFiles
│   ├── config.py               # Settings (env vars, DB URL)
│   ├── database.py             # psycopg3 connection pool
│   ├── maintenance.py          # CLI: backfill-run-times / backfill-usage
│   ├── redaction.py            # Server-side secret redaction
│   ├── models/                 # Pydantic response models
│   ├── routes/
│   │   ├── events.py           # POST /runs/events (ingestion)
│   │   └── reads.py            # GET /runs, /runs/{id}, /runs/{id}/events, /overview
│   ├── schemas/                # Pydantic request/response schemas
│   └── static/                 # Verification UI (served at /ui)
│       ├── index.html          # Overview + runs list + token chart
│       ├── run.html            # Run detail + events drill-down
│       ├── app.js              # Vanilla JS; fetch() against JSON read API
│       ├── charts.js           # Chart.js@4.4.6 (CDN, SRI-pinned) bar chart
│       └── style.css
├── capture/
│   └── claude_code/            # Standalone capture CLI
│       ├── cli.py              # Entry point; --repo, --catch-all, --cwd-map
│       ├── extractor.py        # Transcript read-and-replay
│       ├── identity.py         # git-remote → repository_id resolution
│       ├── normalizer.py       # JSONL → normalized run events
│       ├── overrides.py        # cwd override map loader
│       ├── reclassify.py       # Surgical UPDATE for historical mis-routing
│       └── cwd_overrides.toml  # Version-controlled old-cwd → repo map
├── database/
│   ├── schema.sql              # Postgres schema (runs, run_events, usage_metrics, views)
│   └── seed.sql                # Projects + repos reference data
├── docs/
│   ├── decisions/              # Architecture Decision Records (ADR 0001–0005)
│   └── superpowers/
│       ├── plans/              # SDD implementation plans (one per milestone)
│       └── specs/              # Design specs (one per milestone)
├── tests/                      # pytest; runs against live Postgres
├── docker-compose.yml          # api + postgres services
└── pyproject.toml              # Hatchling build; app/ bundled into wheel
```

### Running the system

```bash
# Start (never use down -v — destroys Postgres volumes)
docker compose up -d --build api

# Capture (from this machine)
set -a; . ./.env; set +a
python -m capture.claude_code.cli --repo agentops-core
python -m capture.claude_code.cli --catch-all <unsorted-repo-id>

# Tests
set -a; . ./.env; set +a; .venv/bin/python -m pytest

# Maintenance
set -a; . ./.env; set +a; python -m app.maintenance backfill-run-times
set -a; . ./.env; set +a; python -m app.maintenance backfill-usage

# Verification UI
open http://localhost:8000/ui/
```

### Key architecture invariants

| Rule | Why |
|---|---|
| PostgreSQL is the system of record | Analytical stores (ClickHouse, Qdrant) are *rebuildable projections* |
| `cost_usd` stays NULL, `cost_source='unavailable'` | No dollar estimation until OTEL `cost_source='reported'` |
| Capture is idempotent via `source_event_id` UNIQUE | Re-running never duplicates; multi-desktop merges are safe |
| Server-side redaction in `app/redaction.py` | Container must be rebuilt after any redaction change |
| NULL renders as `—`, never `0` | Missing-stays-unavailable principle visible in UI |
| No write paths in the UI | `/ui` is read-only; a write path in that code is always a bug |

---

## 3. Completed Milestones

| PR | Merge | What shipped |
|---|---|---|
| #1 | — | Project scaffold; FastAPI skeleton; Docker Compose |
| #2 | — | `POST /runs/events` ingestion endpoint; PostgreSQL schema |
| #3 | `1a6a45c` | Full Claude Code capture stack: transcript read-and-replay, idempotent via source_event_id, sub-agent capture (merged into parent run), reclassification CLI, cwd override map, POST /repositories endpoint, seed.sql for 4 real repos |
| #4 | `968e86c` | Run timestamps: started_at/ended_at derived from MIN/MAX(occurred_at); go-forward fold + whole-table backfill; maintenance CLI |
| #5 | `969c84f` | Read surface: token-usage derivation into usage_metrics; GET /runs, GET /runs/{id}, GET /overview read API |
| #7 | `e9751c0` | Verification UI: GET /runs/{id}/events; /ui static HTML+JS; Chart.js token bar chart; all data via fetch() against JSON API |

**Test suite:** 115/115 passing on `main`.

**Live database:** 106 runs / ~13.2k events across 5 repos (agentops-core, leadle-os, leadle-content-studio, ai-native-revops-work-brain, ai-work-journal); 106 usage_metrics rows; cost_usd all NULL; started 2026-05-05, last captured 2026-06-19.

---

## 4. Challenges Faced

### 4.1 Over-redaction destroyed token data

`SECRET_KEY_HINTS` matched "token" as a substring, so integer `input_tokens` / `output_tokens` / `cache_*_tokens` fields were wholly redacted — destroying the usage data before it reached the DB. **Fix:** redact only *string* values under sensitive keys; recurse into numbers without redacting them.

### 4.2 session_ended synthesis crash (422)

`session_ended` is synthesized from the last line of a transcript, which can be a metadata line lacking `sessionId` → null → `POST /runs/events` returned 422. **Fix:** pass the canonical session id (transcript filename stem) explicitly into the synthesis step rather than relying on the line's `sessionId`.

### 4.3 Sub-agent transcripts produced orphan runs

Sub-agent files (`.../subagents/agent-*.jsonl`) lack a consistent `sessionId` in their lines. The parent session id was derived from the path (`path.parent.parent.name`). Design decision: merge sub-agents into the parent run (shared `session_id` satisfies the UNIQUE key); no schema change needed.

### 4.4 catch-all runs landed with null `cwd`

`session_started` took `cwd` from line[0], which can be a metadata line with no `cwd`. **Fix:** inject session-wide cwd/branch (first non-empty across all lines) into every event, the same way model is injected.

### 4.5 Stacked-PR auto-close (process gotcha)

Merging PR #5 with `gh pr merge --delete-branch` deleted `feat/read-surface`, which was the *base branch* of the stacked PR #6 (verification-ui) → GitHub automatically closed PR #6 with no way to reopen it. **Recovery:** opened a fresh PR #7 from `feat/verification-ui → main`. **Lesson:** when draining a stacked-PR chain, retarget the upper PR's base to `main` *before* deleting the lower branch — or merge bottom-up without `--delete-branch` until the top is retargeted.

### 4.6 Backfill side-effects in tests

The whole-table `backfill_run_time_bounds` test was mutating live DB rows (it ran against the shared Postgres, not a fixture). Fixed by wrapping those tests in an explicit `conn.rollback()` so the backfill SQL ran inside a transaction that was rolled back, not committed.

### 4.7 OTEL cannot replace transcripts

Verified against Claude Code v2.1.183: OTEL standard attributes have no `cwd` and no `gitBranch`, so OTEL alone cannot attribute a session to a repo. Transcripts are non-optional. OTEL is a *complementary enrichment layer* (authoritative `cost_usd`, real-time push, attribution metadata), not a replacement. Join key: `session.id` (OTEL) == `sessionId` (transcript).

---

## 5. Current State

- `main` is clean and fully synced with GitHub at `e9751c0`.
- Docker `agentops-api` container is running locally serving port 8000.
- `/ui` verification surface is live and confirmed working against real data.
- The AGENTS.md first-milestone definition of done has **three gaps remaining**:
  - Codex activity capture
  - Git commit reconciliation
  - Cross-repo structured search

---

## 6. What's Pending

### Gap 1 — Codex capture (AGENTS.md step #6)

Codex stores session logs in a different format than Claude Code transcripts. A second capture adapter (`capture/codex/`) needs to read-and-replay those logs into the same `run_events` schema. This unblocks:
- "Both tools write into the same PostgreSQL" (DoD requirement)
- Claude Code vs Codex comparison (north-star differentiation wedge)

### Gap 2 — Git commit reconciliation (AGENTS.md step #7)

Connect captured runs to the git commits / PRs they produced. This enables:
- Shipped-outcome metrics: cost-per-feature, time-to-merge
- The `git/PR-reconciled` differentiation wedge (north-star)

Approach: query `git log` for the run's time window + branch; store commit SHAs in a new `run_commits` join table; optionally pull PR metadata from GitHub API.

### Gap 3 — Cross-repo structured search (AGENTS.md step #8)

Basic structured search over runs/events spanning ≥2 repositories. Builds directly on the existing read API (whitelist-driven filter columns). Could be as simple as adding a `query` text-search param to `GET /runs`, or as rich as a dedicated `/search` endpoint over `run_events.raw_payload` with GIN indexing.

### Gap 4 — OTEL cost enrichment (north-star, not a DoD blocker)

Wire an OTLP receiver (gRPC :4317) into the API. When a `claude_code.api_request` event arrives, update `usage_metrics.cost_usd` and set `cost_source='reported'`. This moves cost from NULL/unavailable to authoritative dollar amounts. Deferred until the foundation DoD is closed.

---

## 7. Plan to Complete

### Phase A: Close the First-Milestone DoD (in order)

**Step 1 — Codex capture** (new branch `feat/codex-capture`)
- Discover Codex session log format and storage path
- Build `capture/codex/` adapter (same shape as `capture/claude_code/`): extractor + normalizer + identity + CLI
- Normalize Codex events into the same `run_events` schema (event_type names may differ; normalize to the shared vocabulary)
- Add tests; backfill existing Codex sessions; verify in `/ui`

**Step 2 — Git commit reconciliation** (new branch `feat/git-reconciliation`)
- New DB table: `run_commits(run_id, commit_sha, committed_at, author, message, pr_number)`
- New capture step: after a run is ingested, query `git log` for the branch + time window; upsert into `run_commits`
- Expose via read API: `GET /runs/{id}/commits`
- Show in `/ui` run detail

**Step 3 — Cross-repo search** (new branch `feat/search`)
- Add `q` free-text param to `GET /runs` (search `intent`, `summary`, `branch`, `human`)
- Add `GET /search/events?q=` over `run_events` (GIN index on `raw_payload` or tsvector on normalized text fields)
- Verify across ≥2 repos in `/ui`

### Phase B: OTEL Enrichment (after DoD is closed)

- Add OTLP HTTP receiver endpoint to FastAPI
- Parse `claude_code.api_request` span; match `session.id` to a run; update `usage_metrics`
- Update `cost_source` from `'unavailable'` to `'reported'`

### Phase C: AI Work Journal Connection (AGENTS.md step #9)

- The Journal reads from AgentOps Core via the read API
- Define the Journal's read contract (which endpoints, which fields)
- No write paths from Journal back to AgentOps Core

### Phase D: Agent Registry (north-star, separate data domain)

- Agent definitions / versions / bundled MCP + skills + hooks + install metadata
- Completely separate schema from runs/events; design as its own milestone

---

## 8. ADR Index

| ADR | Decision |
|---|---|
| 0001 | PostgreSQL as system of record; no ORM; raw parameterized SQL |
| 0002 | Claude Code transcript read-and-replay (not hooks) |
| 0003 | Normalized meaningful events (not full JSONL faithful replay) |
| 0004 | Sub-agent capture merged into parent run (no schema change) |
| 0005 | cwd override map + reclassification CLI for historical mis-routing |
