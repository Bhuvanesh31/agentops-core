# AgentOps Core — Project Status

**As of:** 2026-06-25  
**Branch:** `main` @ `601fb4e`  
**GitHub:** https://github.com/Bhuvanesh31/agentops-core  
**Stack:** FastAPI + psycopg3 + PostgreSQL · Docker Compose · Python 3.12 · Hatchling  
**Test suite:** 119/119 passing

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
│   ├── claude_code/            # Standalone Claude Code capture CLI
│   │   ├── cli.py              # Entry point; --repo, --catch-all, --cwd-map
│   │   ├── extractor.py        # Transcript read-and-replay
│   │   ├── identity.py         # git-remote → repository_id resolution
│   │   ├── normalizer.py       # JSONL → normalized run events
│   │   ├── overrides.py        # cwd override map loader
│   │   ├── reclassify.py       # Surgical UPDATE for historical mis-routing
│   │   └── cwd_overrides.toml  # Version-controlled old-cwd → repo map
│   └── git/                    # NEW (2026-06-25) — git reconciliation
│       ├── __init__.py         # Package marker
│       └── query.py            # query_commits(cwd, branch, after, before)
├── database/
│   ├── schema.sql              # Postgres schema (runs, run_events, usage_metrics, commits, views)
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

# Git reconciliation (after Task 2 ships)
set -a; . ./.env; set +a; python -m capture.git.reconcile --all
set -a; . ./.env; set +a; python -m capture.git.reconcile --repo agentops-core

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
| All UI data via `textContent`, never `innerHTML` | Raw git / user data is untrusted; XSS guard |
| Git reconciliation is non-fatal | A git failure never blocks capture; logged to stderr and skipped |
| Commits upserted with `ON CONFLICT (commit_sha) DO NOTHING` | Idempotent; newest-first processing means first writer wins |

---

## 3. Completed Milestones

| PR | Merge commit | What shipped |
|---|---|---|
| #1 | — | Project scaffold; FastAPI skeleton; Docker Compose |
| #2 | — | `POST /runs/events` ingestion endpoint; PostgreSQL schema |
| #3 | `1a6a45c` | Full Claude Code capture stack: transcript read-and-replay, idempotent via source_event_id, sub-agent capture (merged into parent run), reclassification CLI, cwd override map, POST /repositories endpoint, seed.sql for 4 real repos |
| #4 | `968e86c` | Run timestamps: started_at/ended_at derived from MIN/MAX(occurred_at); go-forward fold + whole-table backfill; maintenance CLI |
| #5 | `969c84f` | Read surface: token-usage derivation into usage_metrics; GET /runs, GET /runs/{id}, GET /overview read API |
| #7 | `e9751c0` | Verification UI: GET /runs/{id}/events; /ui static HTML+JS; Chart.js token bar chart; all data via fetch() against JSON API |

**Live database:** 106 runs / ~13.2k events across 5 repos (agentops-core, leadle-os, leadle-content-studio, ai-native-revops-work-brain, ai-work-journal); 106 usage_metrics rows; cost_usd all NULL; started 2026-05-05, last captured 2026-06-19.

---

## 4. Active Milestone — Git Commit Reconciliation

**Design spec:** `docs/superpowers/specs/2026-06-25-git-reconciliation-design.md` (commit `a055ff9`)  
**Implementation plan:** `docs/superpowers/plans/2026-06-25-git-reconciliation.md` (commit `77d7b0c`)  
**Execution method:** Subagent-Driven Development (fresh implementer + reviewer per task)

### Goal
Connect captured runs to the git commits they produced. Enables shipped-outcome metrics (cost-per-feature, time-to-merge) and the git/PR-reconciled differentiation wedge.

### Architecture
- `commits` table already exists in schema — **no migration needed**
- Host-side git queries via `subprocess.run git log` — API container has no git access
- Match window: `[run.started_at, run.ended_at + 30 minutes]`
- Ambiguity resolution: process runs `ORDER BY ended_at DESC`; `ON CONFLICT DO NOTHING` → newest-win

### Task Status

| Task | Status | Commit(s) | Files |
|---|---|---|---|
| Task 1: `capture/git/query.py` + conftest | ✅ DONE | `601fb4e` | `capture/git/__init__.py`, `capture/git/query.py`, `tests/test_git_query.py`, `tests/conftest.py` |
| Task 2: `capture/git/reconcile.py` + reconcile CLI | ⏳ PENDING | — | `capture/git/reconcile.py`, `tests/test_git_reconcile.py` |
| Task 3: Post-capture step in `cli.py` | ⏳ PENDING | — | `capture/claude_code/cli.py` |
| Task 4: `GET /runs/{run_id}/commits` read API | ⏳ PENDING | — | `app/routes/reads.py`, `app/models/reads.py`, `app/schemas/reads.py` |
| Task 5: Commits section in run detail UI | ⏳ PENDING | — | `app/static/run.html`, `app/static/app.js` |

**After all 5 tasks:** run `python -m capture.git.reconcile --all` to backfill 106 existing runs.

### Task 1 details (done — `601fb4e`)

- `query_commits(cwd, branch, after, before) -> list[dict]` — never raises; returns `[]` on any git failure; logs to stderr
- 4 tests in `tests/test_git_query.py` — uses real `git init` in tmp dir with controlled commit timestamps via env vars
- `conftest.py` updated: commits cleaned up before runs in `cleanup_pytest_rows` (FK is SET NULL, not CASCADE)
- 119/119 passing

---

## 5. What's Pending

### Pending 1 — Remaining git reconciliation tasks (Tasks 2–5)

**Task 2** — `capture/git/reconcile.py` + CLI

Create the reconcile module:
- `upsert_commits(conn, run_id, repository_id, commits: list[dict])` — `INSERT ... ON CONFLICT (commit_sha) DO NOTHING`
- `reconcile_runs(conn, repo_filter=None)` — queries runs WHERE `ended_at IS NOT NULL AND branch IS NOT NULL AND cwd IS NOT NULL ORDER BY ended_at DESC`, calls `query_commits`, upserts
- `main(argv)` CLI — `--repo`, `--all`, `--dry-run` flags; prints per-run summary + totals
- 3 tests: idempotent upsert, conflict-do-nothing (run A wins), end-to-end reconcile

**Task 3** — Post-capture git step in `capture/claude_code/cli.py`

After `process()` returns, call `_reconcile_after_capture(session_id, repository_id, tool_id)`. Non-fatal: any exception is logged to stderr, never re-raised, never changes the exit code. Skipped on `--dry-run`.

**Task 4** — `GET /runs/{run_id}/commits` read endpoint

Add `RunCommit(BaseModel)` schema, `list_run_commits(conn, run_id)` model, and `GET /runs/{run_id}/commits` route to reads.py. Returns `list[RunCommit]` ordered `committed_at ASC NULLS LAST`. Returns `[]` for unknown run (not 404). 2 tests.

**Task 5** — Commits section in run detail UI

Add `<section id="commits-section">` with a `<table>` to `run.html`. Add `renderCommits()` function to `app.js` — all values via `textContent`, never `innerHTML`. Wire into `initRun()` as a third fetch after events. SHA displayed as 7-char `<code>`.

### Pending 2 — Cross-repo structured search (AGENTS.md step #8)

Not started. Will be brainstormed separately after git reconciliation ships. Design options:
- Add `q` free-text param to `GET /runs` (searches `intent`, `summary`, `branch`, `human`)
- Add dedicated `GET /search/events?q=` with GIN tsvector index on `run_events`
- Choice of single endpoint vs. separate `/search` is the first brainstorm question

### Pending 3 — Codex capture (AGENTS.md step #6, DEFERRED)

Deferred — no Codex activity yet. Will be tackled once git reconciliation + search are done.

---

## 6. Plan to Complete

### Phase A: Close the First-Milestone DoD (in order)

**Step 1 — Git commit reconciliation** (active, Tasks 2–5 remaining)
- Run SDD: dispatch Task 2 implementer → reviewer → Task 3 → 4 → 5 → final whole-branch review → push + PR → merge
- Progress ledger: `.superpowers/sdd/progress.md`
- BASE commit for review packages: `601fb4e`

**Step 2 — Cross-repo structured search** (`feat/search` branch)
- Brainstorm → spec → plan → SDD execution
- Builds directly on the existing read API whitelist pattern (`_FILTER_COLUMNS`)

**Step 3 — Codex capture** (`feat/codex-capture` branch)
- Discover Codex session log format and storage path
- Build `capture/codex/` adapter (same shape as `capture/claude_code/`): extractor + normalizer + identity + CLI
- Normalize into the same `run_events` schema; add tests; backfill existing Codex sessions

### Phase B: OTEL Enrichment (after DoD closed)

- OTLP HTTP receiver endpoint in FastAPI (gRPC :4317 or HTTP :4318)
- Parse `claude_code.api_request` span; match `session.id` → run; update `usage_metrics.cost_usd`
- Flip `cost_source` from `'unavailable'` to `'reported'`

### Phase C: AI Work Journal Connection

- Journal reads from AgentOps Core via the read API
- Define the read contract (which endpoints, which fields); no write paths back

### Phase D: Agent Registry (separate data domain)

- Agent definitions / versions / bundled MCP + skills + hooks + install metadata
- Completely separate schema from runs/events; own milestone

---

## 7. Next Session — Pick-Up Instructions

1. **Resume git reconciliation at Task 2** (`capture/git/reconcile.py`)
   ```bash
   # Extract task brief
   /home/bhuvanesh/.claude/plugins/cache/claude-plugins-official/superpowers/6.0.3/skills/subagent-driven-development/scripts/task-brief \
     docs/superpowers/plans/2026-06-25-git-reconciliation.md 2
   # BASE commit (record before dispatching implementer)
   git log --oneline -1   # should show 601fb4e
   ```
   - Dispatch implementer (sonnet — multi-file integration task with DB writes + CLI)
   - After implementer reports DONE: run `review-package 601fb4e HEAD`, dispatch reviewer

2. **After all 5 tasks:** rebuild container and backfill:
   ```bash
   docker compose up -d --build api
   set -a; . ./.env; set +a; python -m capture.git.reconcile --all
   ```

3. **After git reconciliation ships:** start cross-repo search brainstorm session.

4. **Progress ledger** is at `.superpowers/sdd/progress.md` — append one line per task when reviewed clean.

---

## 8. Challenges Faced

### 8.1 Over-redaction destroyed token data

`SECRET_KEY_HINTS` matched "token" as a substring, so integer `input_tokens` / `output_tokens` / `cache_*_tokens` fields were wholly redacted — destroying the usage data before it reached the DB. **Fix:** redact only *string* values under sensitive keys; recurse into numbers without redacting them.

### 8.2 session_ended synthesis crash (422)

`session_ended` is synthesized from the last line of a transcript, which can be a metadata line lacking `sessionId` → null → `POST /runs/events` returned 422. **Fix:** pass the canonical session id (transcript filename stem) explicitly into the synthesis step rather than relying on the line's `sessionId`.

### 8.3 Sub-agent transcripts produced orphan runs

Sub-agent files (`.../subagents/agent-*.jsonl`) lack a consistent `sessionId` in their lines. The parent session id was derived from the path (`path.parent.parent.name`). Design decision: merge sub-agents into the parent run (shared `session_id` satisfies the UNIQUE key); no schema change needed.

### 8.4 catch-all runs landed with null `cwd`

`session_started` took `cwd` from line[0], which can be a metadata line with no `cwd`. **Fix:** inject session-wide cwd/branch (first non-empty across all lines) into every event, the same way model is injected.

### 8.5 Stacked-PR auto-close (process gotcha)

Merging PR #5 with `gh pr merge --delete-branch` deleted `feat/read-surface`, which was the *base branch* of the stacked PR #6 (verification-ui) → GitHub automatically closed PR #6 with no way to reopen it. **Recovery:** opened a fresh PR #7. **Lesson:** retarget the upper PR's base to `main` *before* deleting the lower branch.

### 8.6 Backfill side-effects in tests

The whole-table `backfill_run_time_bounds` test was mutating live DB rows (it ran against the shared Postgres, not a fixture). Fixed by wrapping those tests in an explicit `conn.rollback()` so the backfill SQL ran inside a transaction that was rolled back, not committed.

### 8.7 OTEL cannot replace transcripts

Verified against Claude Code v2.1.183: OTEL standard attributes have no `cwd` and no `gitBranch`, so OTEL alone cannot attribute a session to a repo. Transcripts are non-optional. OTEL is a *complementary enrichment layer* (authoritative `cost_usd`, real-time push, attribution metadata), not a replacement. Join key: `session.id` (OTEL) == `sessionId` (transcript).

---

## 9. ADR Index

| ADR | Decision |
|---|---|
| 0001 | PostgreSQL as system of record; no ORM; raw parameterized SQL |
| 0002 | Claude Code transcript read-and-replay (not hooks) |
| 0003 | Normalized meaningful events (not full JSONL faithful replay) |
| 0004 | Sub-agent capture merged into parent run (no schema change) |
| 0005 | cwd override map + reclassification CLI for historical mis-routing |
