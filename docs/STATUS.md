# AgentOps Core — Project Status

**As of:** 2026-07-22  
**Branch:** `main` @ `8a8c1df` → operationalization script added  
**GitHub:** https://github.com/Bhuvanesh31/agentops-core  
**Stack:** FastAPI + psycopg3 + PostgreSQL · Docker Compose · Python 3.12 · Hatchling  
**Test suite:** 59 test functions across 15 files (last recorded full-suite run: 119/119 at `601fb4e`; Tasks 2–5 have since added 7 test functions — rerun to restate the collected number)

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
│   └── git/                    # Git reconciliation (shipped 2026-07-14/15)
│       ├── __init__.py         # Package marker
│       ├── query.py            # query_commits(cwd, branch, after, before)
│       └── reconcile.py        # upsert_commits, reconcile_runs, CLI (--repo/--all/--dry-run)
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

# Git reconciliation (shipped — runs automatically after every capture; this is for manual/backfill use)
set -a; . ./.env; set +a; python -m capture.git.reconcile --all
set -a; . ./.env; set +a; python -m capture.git.reconcile --repo <repository_id>   # filters on repository_id, not a path

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
| — | `601fb4e` | Git reconciliation Task 1: `capture/git/query.py` (`query_commits`), commit cleanup in conftest |
| — | `a6beb04`, `0b45c65` | Git reconciliation Task 2: `capture/git/reconcile.py` — `upsert_commits`, `reconcile_runs`, `main()` CLI (`--repo`, `--all`, `--dry-run`) |
| — | `6bb72d6` | Git reconciliation Task 3: post-capture `_reconcile_after_capture()` hook wired into `capture/claude_code/cli.py` — non-fatal, skipped on `--dry-run` |
| — | `76d40a2` | Git reconciliation Task 4: `GET /runs/{run_id}/commits` read endpoint (`RunCommit` schema, `list_run_commits`) |
| — | `1aa35f0` | Git reconciliation Task 5: commits section in run-detail UI (`renderCommits()` in `app.js`, `#commits` in `run.html`) |
| — | `f53edb0` | Repo prepped for public release |
| — | `8a8c1df` | README updated to document the shipped git reconciliation feature |
| — | — | `scripts/agentops_capture_cron.sh`: scheduled capture wrapper (health-checks API, loads .env silently, appends to `logs/`); `docs/operations.md` added |

**Live database (last recorded count):** 106 runs / ~13.2k events across 5 repos (agentops-core, leadle-os, leadle-content-studio, ai-native-revops-work-brain, ai-work-journal); 106 usage_metrics rows; cost_usd all NULL; started 2026-05-05, last captured 2026-06-19. See §4 note on historical backfill.

---

## 4. Completed Milestone — Git Commit Reconciliation ✅

**Design spec:** `docs/superpowers/specs/2026-06-25-git-reconciliation-design.md` (commit `a055ff9`)  
**Implementation plan:** `docs/superpowers/plans/2026-06-25-git-reconciliation.md` (commit `77d7b0c`)  
**Execution method:** Subagent-Driven Development (fresh implementer + reviewer per task)  
**Shipped:** all 5 tasks complete as of `8a8c1df` (2026-07-15)

### Goal
Connect captured runs to the git commits they produced. Enables shipped-outcome metrics (cost-per-feature, time-to-merge) and the git/PR-reconciled differentiation wedge.

### Architecture
- `commits` table already exists in schema — **no migration needed**
- Host-side git queries via `subprocess.run git log` — API container has no git access
- Match window: `[run.started_at, run.ended_at + 30 minutes]`
- Ambiguity resolution: process runs `ORDER BY ended_at DESC`; `ON CONFLICT DO NOTHING` → newest-win

### Task Status — all done

| Task | Status | Commit(s) | Files |
|---|---|---|---|
| Task 1: `capture/git/query.py` + conftest | ✅ DONE | `601fb4e` | `capture/git/__init__.py`, `capture/git/query.py`, `tests/test_git_query.py`, `tests/conftest.py` |
| Task 2: `capture/git/reconcile.py` + reconcile CLI | ✅ DONE | `a6beb04`, `0b45c65` | `capture/git/reconcile.py`, `tests/test_git_reconcile.py`, `tests/test_git_reconcile_cli.py` |
| Task 3: Post-capture step in `cli.py` | ✅ DONE | `6bb72d6` | `capture/claude_code/cli.py` (`_reconcile_after_capture()`) |
| Task 4: `GET /runs/{run_id}/commits` read API | ✅ DONE | `76d40a2` | `app/routes/reads.py`, `tests/test_reads_commits.py` |
| Task 5: Commits section in run detail UI | ✅ DONE | `1aa35f0` | `app/static/run.html`, `app/static/app.js` |

### What each task actually delivers (verified against code)

- **Task 1** — `query_commits(cwd, branch, after, before) -> list[dict]` — never raises; returns `[]` on any git failure; logs to stderr. 4 tests in `tests/test_git_query.py` using a real `git init` in a tmp dir with controlled commit timestamps.
- **Task 2** — `upsert_commits()` (`INSERT ... ON CONFLICT (commit_sha) DO NOTHING`) and `reconcile_runs(conn, repository_id=None, dry_run=False)` — queries runs `WHERE ended_at IS NOT NULL AND branch IS NOT NULL AND cwd IS NOT NULL ORDER BY ended_at DESC`, matches each against `query_commits`, upserts. `main(argv)` CLI exposes `--repo REPOSITORY_ID`, `--all` (default), `--dry-run`. Returns `{"runs_processed": int, "commits_linked": int}`; DB query failures are caught and logged, never raised.
- **Task 3** — `_reconcile_after_capture()` in `capture/claude_code/cli.py` runs after every successful ingest, imports `reconcile_runs` lazily, and is wrapped so any exception is caught, logged to stderr, and never changes the capture exit code.
- **Task 4** — `GET /runs/{run_id}/commits` returns `list[RunCommit]`.
- **Task 5** — `run.html` has a `#commits` section; `app.js` fetches `/runs/{id}/commits` and calls `renderCommits()`, which shows "No commits linked." when the list is empty.

### Note on historical backfill

Reconciliation now runs **automatically after every new capture** (Task 3). The 106 runs already in the live database as of the last recorded count were captured before this hook existed; whether they've since been backfilled with `python -m capture.git.reconcile --all` was not checked while writing this update (no DB query was run). Run that command manually if older runs are missing commit links.

---

## 5. What's Pending

Git commit reconciliation (§4) is done. The active gap is operational, not architectural: **capture has no scheduled job**. `python -m capture.claude_code.cli` still has to be run by hand — nothing pulls new transcripts into the database on its own. Everything downstream (read API, verification UI, git reconciliation) only reflects sessions someone remembered to capture.

### Next Milestone — AgentOps Operationalization

**1. Scheduled capture (top priority)**

Turn `capture/claude_code/cli.py` into an unattended job — e.g. a cron entry or systemd timer that runs `python -m capture.claude_code.cli --repo <id>` (or `--catch-all`) on a recurring interval, the same pattern already in production for `devlog ingest` (nightly cron) and `leadle-mom-automation` (15-minute systemd timer) elsewhere in this workspace. Once capture is scheduled, git reconciliation — already automatic per-capture (Task 3) — keeps commit links current with no further work.

**2. Safe proof-summary / export for Content Intelligence**

A read-only export surface that the `bhuvanesh-content-intelligence` work-corpus module (and similar consumers) can pull from without touching the database directly or seeing raw/sensitive event payloads — e.g. a summarized JSON/Markdown extract (repo, date range, run counts, token totals, shipped-commit counts) built from the existing read API, with redaction already applied. Not started; needs its own brainstorm on shape (which fields are safe to expose, what "proof" means for that consumer).

**3. Cross-repo structured search (later, optional)**

Deprioritized relative to the two items above. Not started. Design options on the table if picked back up:
- Add a `q` free-text param to `GET /runs` (searches `intent`, `summary`, `branch`, `human`)
- A dedicated `GET /search/events?q=` with a GIN tsvector index on `run_events`
- Choice of single endpoint vs. separate `/search` is the first brainstorm question

### Still deferred — Codex capture

No Codex activity yet observed. Remains deferred behind the operationalization milestone above.

---

## 6. Plan to Complete

### Phase A: First-Milestone DoD — ✅ CLOSED

Git commit reconciliation (Tasks 1–5) shipped `8a8c1df`, 2026-07-15. No remaining work on this milestone.

### Phase A2: AgentOps Operationalization (active)

**Step 1 — Scheduled capture** ✅ DONE
- `scripts/agentops_capture_cron.sh` written — health-checks API, loads `.env` silently, runs capture, logs to `logs/agentops_capture.log`
- Cron line documented in `docs/operations.md` (install manually with `crontab -e`)
- Once cron is installed and stable, run `python -m capture.git.reconcile --all` once to backfill any pre-hook runs

**Step 2 — Safe proof-summary / export for Content Intelligence**
- Brainstorm the export shape with the Content Intelligence consumer in mind (what "proof" needs to look like, which fields are safe post-redaction)
- Spec → plan → SDD execution once shape is agreed

**Step 3 — Cross-repo structured search** (later, optional; `feat/search` branch)
- Brainstorm → spec → plan → SDD execution
- Builds directly on the existing read API whitelist pattern (`_FILTER_COLUMNS`)

**Step 4 — Codex capture** (deferred; `feat/codex-capture` branch)
- Discover Codex session log format and storage path
- Build `capture/codex/` adapter (same shape as `capture/claude_code/`): extractor + normalizer + identity + CLI
- Normalize into the same `run_events` schema; add tests; backfill existing Codex sessions

### Phase B: OTEL Enrichment (after operationalization)

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

1. **Install the cron job** (Step 1 of Operationalization is scripted; you just need to wire it up):
   ```
   crontab -e
   # Add: */30 * * * * /home/bhuvanesh/AI_Native_Workspace/10-platform/agentops_core/scripts/agentops_capture_cron.sh
   ```
   Then run `python -m capture.git.reconcile --all` once to backfill any runs captured before the post-capture hook (Task 3) existed. See `docs/operations.md` for the full runbook.

2. **Then move to Step 2 — safe proof-summary / export for Content Intelligence.** Brainstorm the export shape with that consumer's actual needs before speccing.

3. **Cross-repo search and Codex capture stay deferred** behind the two steps above — do not start them first.

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
