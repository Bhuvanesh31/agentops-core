# Claude Code Capture — Design Spec

**Date:** 2026-06-19
**Status:** Proposed (awaiting review)
**Milestone:** Build order step 5 — "Add Claude Code capture" (after the FastAPI ingestion foundation, PR #2)

## 1. Goal

Capture work performed by Claude Code into AgentOps Core by reading the local
session transcripts Claude Code already writes to disk, normalizing them into the
shared run-event model, and submitting them to the existing
`POST /runs/events` ingestion endpoint.

This is the first capture adapter. It must work across multiple desktops and
must be safe to run repeatedly.

## 2. Context & constraints

- North-star: an Observal-class self-hosted agent observability + registry
  platform. See `memory/agentops-north-star.md`. Capture is the current phase.
- `AGENTS.md` build order puts Claude Code capture (step 5) before Codex (step 6)
  and before Git reconciliation (step 7) and search (step 8).
- Explicit scope guards carried from the ingestion task: **no Claude Code or
  Codex hooks yet**, no Qdrant, no Work Journal UI, no performance scoring.
- The ingestion service is **tool-agnostic and already Codex/Claude-ready**:
  `POST /runs/events` validates that `tool` exists in the `tools` table and
  writes one row to `run_events`. `claude-code` is already seeded.

## 3. Source data

Claude Code stores each session as a JSONL transcript:

```
~/.claude/projects/<cwd-slug>/<session-uuid>.jsonl
```

(~205 such files exist on the primary desktop as of 2026-06-19.)

Each line is one JSON object. Verified fields relevant to capture:

- Common: `type`, `sessionId`, `cwd`, `gitBranch`, `timestamp`, `version`,
  `entrypoint`, `uuid` (globally unique per line), `parentUuid`.
- `assistant`: `message.model`, `message.usage`
  (`input_tokens`, `output_tokens`, `cache_read_input_tokens`,
  `cache_creation_input_tokens`), `message.content` (blocks: `thinking`,
  `text`, `tool_use` with `name` + `input`), `requestId`.
- `user`: `message.content` — a **string** for real prompts, or a **list** of
  `tool_result` blocks for synthetic tool-result turns; `isMeta`,
  `promptSource`, `toolUseResult`.
- `pr-link`: `prNumber`, `prRepository`, `prUrl`.

Line types observed: `assistant`, `user`, `attachment`, `system`,
`file-history-snapshot`, `mode`, `permission-mode`, `last-prompt`, `ai-title`,
`pr-link`, `bridge-session`.

## 4. Architecture

A **standalone Python CLI replay tool** inside this repo, separate from the API
service:

```
capture/
  claude_code/
    __init__.py
    cli.py            # entrypoint + arg parsing
    discovery.py      # find & enumerate transcript files
    identity.py       # cwd -> canonical git remote -> repo/project resolution
    normalize.py      # transcript line -> NormalizedEvent dict (the mapping)
    client.py         # POST to /runs/events (httpx), ret/backoff
    report.py         # run summary + pending-repos report
  tests/
    ...
```

Rationale (decided in brainstorming, see `memory/claude-capture-design-decisions.md`):
chosen over live hooks (out of scope; cannot backfill) and a long-running
file-watcher daemon (more operational complexity than a periodic re-scan needs).
The tool reuses `app/redaction.py` rather than re-implementing redaction.

### Component boundaries

| Component | Does | Depends on |
|---|---|---|
| `discovery` | Enumerate `~/.claude/projects/**/*.jsonl`; optional filters (project slug, since-mtime) | filesystem |
| `identity` | Resolve a transcript's `cwd` to a canonical git remote, then to a registered `(project_id, repository_id)`; classify unknown/local-only | `git`, API (`GET /repositories`) |
| `normalize` | Map each meaningful line to a `NormalizedEvent`; drop noise; derive `files_touched` | redaction |
| `client` | Submit events to `POST /runs/events`; interpret created/duplicate; retry transient errors | API |
| `report` | Per-run counts; list of skipped/pending repos with next-step guidance | — |

Each is independently testable: `normalize` and `identity` are pure functions
over inputs; `discovery` over a temp dir; `client` against a stub.

## 5. Data flow

```
for each transcript file:
  resolve identity (cwd -> git remote -> repo/project)
    ├─ registered      -> proceed
    ├─ remote, unknown -> record to pending-repos report, SKIP file
    └─ no remote       -> record to local-only report, SKIP file (opt-in only)
  for each line in file:
    classify type
      ├─ noise type     -> skip
      └─ meaningful     -> normalize -> redact raw_payload -> POST /runs/events
  accumulate created/duplicate/error counts
emit summary + pending/local-only reports
```

## 6. Normalization mapping (transcript line → normalized event)

Grain: **normalized meaningful events only** (decided in brainstorming). The
redacted raw line is attached as `raw_payload`; `source_event_id` is the line's
`uuid` (globally unique → idempotent across re-runs and desktops).

| Transcript line | → `event_type` | Notes |
|---|---|---|
| `user`, content is **string**, not `isMeta` | `user_prompt` | the human prompt |
| `assistant` | `assistant_message` | sets run `model`; `message.usage` retained in `raw_payload` |
| `assistant` `tool_use` block: `Edit`/`Write`/`MultiEdit`/`NotebookEdit` | `file_edit` | `file_path` → `files_touched` |
| `assistant` `tool_use` block: `Bash` | `command_run` | command kept in `raw_payload` (redacted) |
| `assistant` `tool_use` block: other tools | `tool_use` | generic |
| `pr-link` | `pr_link` | `prUrl`/`prNumber`/`prRepository` in `raw_payload` |
| first line of file (earliest `timestamp`) | `session_started` | synthesized once per run |
| end of file | `session_ended` | synthesized once per run from last `timestamp`, **only when the file is idle** (mtime older than a threshold, default 1h) so in-progress sessions are not marked ended early |
| `user` with list/`tool_result` content, `isMeta`, `toolUseResult` | — | **dropped** (noise / synthetic) |
| `attachment`, `mode`, `permission-mode`, `last-prompt`, `ai-title`, `bridge-session`, `file-history-snapshot`, `system` | — | **dropped** (UI/bookkeeping) |

### `source_event_id` derivation (one line may emit several events)

An `assistant` line can produce an `assistant_message` plus one event per
`tool_use` block, so events cannot all share the line `uuid`. Rules:

- `assistant_message`, `user_prompt`, `pr_link` → `source_event_id = <line uuid>`.
- `file_edit` / `command_run` / `tool_use` (from a `tool_use` block) →
  `source_event_id = <tool_use block id>` (Claude's `toolu_…` ids are unique).
- synthesized `session_started` / `session_ended` →
  `source_event_id = "<sessionId>#session_started"` / `"#session_ended"`.

All are stable across re-runs, preserving the `UNIQUE(source_event_id)`
idempotency guarantee.

Run-level fields sent on every event (used by `get_or_create_run` on first
insert): `tool="claude-code"`, `session_id=sessionId`, `repository_id`,
`project_id`, `model` (from assistant lines), `branch=gitBranch`, `cwd`,
`intent` (left null in v1 — no reliable transcript source).

`occurred_at` = line `timestamp`. `files_touched` derived per event from tool
inputs.

## 7. Repository / project identity

Decided: key on **canonical git remote URL**, not `cwd` (machine-specific).

1. On start, the adapter fetches the registry via **`GET /repositories`**
   (a minimal read-only endpoint added in v1) and builds a map:
   canonical-remote → `(project_id, repository_id)`. This is the one mechanism
   that works from any desktop (remote machines reach the central host's API,
   not its DB).
2. `git -C <cwd> remote get-url origin` → canonicalize (`https`/`ssh` →
   `github.com/<owner>/<repo>`, strip `.git`).
3. Look up the canonical remote in the registry map (canonicalized on both
   sides).
4. Outcomes:
   - **Registered** → ingest.
   - **Has remote, not registered** → skip file, add `github.com/owner/repo`
     (with observed `cwd` and session count) to the **pending-repos report**.
   - **No remote** (local-only / personal) → skip file, add to a separate
     **local-only report**; opt-in only.

**Registration is a host-side operation** (the central store): the user adds the
repo to the seed (same pattern as `database/seed.sql`) and re-runs it, then
re-runs the adapter (dedup makes replay safe). The adapter never auto-creates
repositories. A future `POST /repositories` endpoint is optional and out of
scope here.

Note: matching by `remote_url` benefits from a canonical form. The current
`repositories.remote_url` seed value is a full `https://...git` URL; the
resolver canonicalizes both sides before comparing, so existing seed rows work
without schema changes.

## 8. Idempotency & multi-desktop

- `source_event_id = uuid` (per line) → `run_events` `UNIQUE(source_event_id)`
  makes every re-run and every desktop converge with no duplicates.
- `session_id` (the transcript UUID) is globally unique →
  `runs UNIQUE(tool_id, repository_id, session_id)` merges correctly.
- Each desktop runs the adapter against its own `~/.claude` and POSTs to the
  central host (this desktop). Repo identity via git remote means the same repo
  cloned on two desktops maps to one `repository_id`.

## 9. Error handling

- Per-line normalize/parse error → count it, attach context, continue (one bad
  line never aborts a file). Optionally surface to `ingestion_failures` later;
  v1 reports counts.
- Transient API errors (5xx, connection) → retry with bounded backoff in
  `client`; on exhaustion, record and continue.
- `404` from the API (unknown repo/tool) should not happen for resolved repos;
  if it does, it's surfaced loudly (registry drift) and the file is skipped.
- Reports are always emitted, even on partial failure.

## 10. Token / cost handling (v1 boundary)

- Token usage is **captured inside `run_events.raw_payload`** on
  `assistant_message` events (`message.usage`). Nothing is lost.
- v1 does **not** populate the `usage_metrics` table. The only API change in v1
  is one **read-only** `GET /repositories` (for identity resolution, §7); no
  write/registration or usage endpoints. Aggregating per-run token totals (and
  estimating cost →
  `cost_source="estimated"`) is a deliberate follow-up — and is exactly where
  the OTEL path will later write authoritative `cost_source="reported"` values.
  This keeps cost honest per `AGENTS.md` (missing cost = unavailable, never zero).

## 11. CLI interface

```
python -m capture.claude_code \
  --api-url http://localhost:8000 \
  [--projects-dir ~/.claude/projects] \
  [--only <cwd-slug-or-repo>] \
  [--since <ISO8601>] \
  [--dry-run] \
  [--machine-name <name>]
```

- `--dry-run` normalizes and reports without POSTing (safe inspection).
- `--since` limits to files modified after a timestamp (cheap incremental runs).
- Config (API URL, machine name) also via env for unattended/timed runs.

## 12. Testing strategy

Reuses the live Postgres container, `pytest-` cleanup pattern from the existing
suite.

- `normalize`: table-driven unit tests, one synthetic line per mapping row,
  including noise lines (asserted dropped) and `files_touched` derivation.
- `identity`: canonicalization (`https`/`ssh`/trailing `.git`), registered hit,
  has-remote-unknown, no-remote — against a temp git repo.
- `redaction`: a transcript line carrying a secret is redacted before POST
  (reuses `app/redaction.py`).
- Idempotency: replay the same file twice → second run is all `duplicate`,
  no new rows.
- End-to-end: a small fixture transcript (sanitized) → run adapter → assert
  one run + expected event rows in the DB; re-run → no growth.
- `--dry-run`: emits report, writes nothing.

Fixtures are **sanitized** synthetic transcripts checked into `capture/tests/`,
not real session files.

## 13. OTEL enrichment — roadmap (NOT v1)

Verified against Claude Code v2.1.183 (`code.claude.com/docs/en/monitoring-usage`).
OTEL is a **complementary future enrichment layer, not a replacement** for
transcript replay:

- OTEL has **no `cwd`/`gitBranch`** attributes → cannot attribute a session to a
  repo on its own. Transcripts can. OTEL also redacts content by default and
  cannot backfill.
- OTEL is **better** for authoritative dollar cost
  (`claude_code.api_request.cost_usd` + token breakdown → `cost_source="reported"`),
  real-time (5s/60s push), and roll-ups (`commit.count`, `pull_request.count`).
- **Join keys:** OTEL `session.id` == transcript `sessionId`;
  OTEL `api_request.request_id` == transcript `assistant.requestId`.
- Transport is **OTLP** (gRPC :4317 / HTTP-protobuf :4318) → needs an OTEL
  Collector or an OTLP receiver added to the API; that is the bulk of the future
  work.

Planned division of labor: transcripts establish the run (repo, branch, content,
events); OTEL enriches `usage_metrics` and powers real-time, keyed by
`session.id` / `request_id`.

## 14. Scope

**In scope (v1):**
- Read local transcripts, resolve repo identity, normalize meaningful events,
  redact, POST to `/runs/events`, report pending/local-only repos.
- One minimal **read-only** `GET /repositories` endpoint for identity resolution.
- Full backfill + go-forward; idempotent; multi-desktop via the central host.

**Out of scope (v1):**
- Hooks; OTEL ingestion; `usage_metrics` aggregation; `commits` table population
  (Git reconciliation is step 7); a repo-**registration** (write) API endpoint
  — registration stays a host-side seed operation; Codex; Work Journal UI;
  scoring/evals; Qdrant.

## 15. Assumptions & open questions

- **Assumption:** transcript `sessionId` equals the filename UUID and is stable
  per session. (Observed true.)
- **Assumption:** `cwd` on transcript lines reflects the repo root or a path
  inside it, so `git -C <cwd>` resolves the remote. Sessions whose folder was
  since deleted cannot resolve a remote → treated as local-only/skipped.
- **Open:** exact canonical form for non-GitHub remotes (GitLab/Bitbucket/SSH
  hosts). v1 canonicalizes GitHub cleanly and stores others verbatim-canonical;
  refine if/when non-GitHub repos are onboarded.
- **Open:** whether `session_started`/`session_ended` should be synthesized by
  the adapter (chosen) or derived later by a view. Adapter-synthesized keeps the
  event stream self-describing.
