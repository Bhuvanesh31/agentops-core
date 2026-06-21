# Sub-agent Capture — Design Spec

**Date:** 2026-06-19
**Status:** Proposed (awaiting review)
**Builds on:** Claude Code transcript capture (PR #3)

## 1. Goal

Capture Claude Code **sub-agent** (Task/Agent-tool) sessions, which the current
adapter misses entirely. Discovery uses `*/*.jsonl` (depth 1); sub-agent
transcripts live one directory deeper, so >50% of all transcript files
(135 nested vs 121 top-level on the primary desktop) are currently uncaptured —
exactly the multi-agent activity an observability platform most wants.

## 2. Context & constraints

- North star: an Observal-class agent observability platform; sub-agent activity
  is first-class signal. See `memory/agentops-north-star.md`.
- Capture phase only: **no scoring/evals yet**, so first-class per-sub-agent
  runs (with their own status/outcome) are premature.
- Reuse the existing capture units (`discovery`, `identity`, `normalize`,
  `client`, `report`, `cli`); change `capture/` only.
- Idempotency, redaction, git-remote identity, and the catch-all path all remain
  as built. See `2026-06-19-claude-code-capture-design.md`.

## 3. Source data (verified)

Sub-agent transcripts: `~/.claude/projects/<slug>/<parent-session-uuid>/subagents/agent-<id>.jsonl`

- **All 135 nested files** follow this single pattern (parent dir is always
  named `subagents`; the directory above it is the parent session UUID).
- Lines use the **same format** as main transcripts (`user`, `assistant`,
  `attachment`), so `normalize_line` works unchanged.
- Every line carries `cwd`, `gitBranch`, `uuid`, `timestamp`, `agentId`,
  `isSidechain: true`; assistant lines also carry `attributionAgent` (the agent
  type) and `requestId`.
- **Critically: each line's `sessionId` is the *parent* session UUID** (equal to
  the directory above `subagents/`), not the agent's id.

## 4. Decision: merge into the parent run (no schema change)

Sub-agent events attach to the parent session's run (they already share its
`sessionId`, which is the run natural key `UNIQUE(tool_id, repository_id,
session_id)`). Agent attribution is preserved in `run_events.raw_payload`
(`agentId`, `attributionAgent`, `isSidechain`) and is queryable via the existing
GIN index.

Rejected for now:
- *Separate run per sub-agent + `parent_run_id`*: a schema migration and a
  parent→child tree. Premature in the capture phase; promote to this later when
  the registry/insights milestone defines the queries.
- *Denormalized `agent_id`/`is_sidechain` columns on `run_events`*: a smaller
  migration, still unnecessary while `raw_payload` answers the same queries.

The feature is therefore essentially **one discovery line + one identity rule**.

## 5. Architecture / components (capture/ only)

### 5.1 `discovery.py`
- Additionally match `*/*/subagents/*.jsonl` and return those paths alongside
  the existing `*/*.jsonl` results.
- The `only` (folder substring) and `since` (mtime) filters apply to both.
- Result ordering must put **top-level (parent) files before sub-agent files**
  so the parent creates the run with its own identity (see §5.2). Sorting by
  path depth (then path) achieves this: a parent `<slug>/<uuid>.jsonl` sorts
  before its `<slug>/<uuid>/subagents/...` children.

### 5.2 `cli.py`
A file is a **sub-agent file** when its immediate parent directory is named
`subagents`. For sub-agent files:
- **Session id = the parent session UUID** = `path.parent.parent.name` (the
  directory above `subagents/`), *not* `path.stem` (which is `agent-<id>` and
  would fork a bogus run). This is what lands the events on the parent run.
- **Do not synthesize `session_started`/`session_ended`** — the parent file owns
  the session boundaries. Only `normalize_line` output is posted.
- Everything else (repo resolution by `cwd`, identity injection of
  `tool`/`project_id`/`repository_id`, session-wide `model`/`cwd`/`branch`,
  redaction, idempotent posting) is unchanged.

For top-level files, behavior is exactly as today (filename as session id, with
synthesized boundaries).

### 5.3 `report.py`
- Add a `sub_agent_files` counter and surface it in `render()` (e.g.
  `files: processed=N sub_agents=M catch_all=K skipped=J`) so sub-agent coverage
  is visible and never silently bundled.

## 6. Data flow

```
discover (top-level + */*/subagents/*) , parents-before-children
for each file:
  lines = load_lines(file)
  cwd   = first line with a cwd
  resolution = resolve_repository(cwd, registry)   # registered / catch-all / skip
  if file is sub-agent:
      session_id = parent-session uuid (dir above subagents/)
      events = normalize_line over lines            # no boundary synthesis
  else:
      session_id = file stem
      events = synthesize_boundaries + normalize_line over lines
  inject identity (tool, project_id, repository_id, session-wide model/cwd/branch)
  post each event   # get_or_create_run attaches to the (parent) run; dedup by source_event_id
```

## 7. Identity & ordering details

- Parent run is created by the parent file (processed first) with the parent's
  `cwd`/`model`. Sub-agent events then call `get_or_create_run` and find it.
- If a sub-agent file is somehow processed with no parent file present (parent
  deleted but sub-agent dir remains), `get_or_create_run` still creates the run
  from the sub-agent's own `cwd`/`model` — no data lost, just a run with only
  sub-agent events. Acceptable.
- Sub-agent `source_event_id`s are the lines' own `uuid`s (globally unique), so
  cross-file dedup holds. No synthesized boundary ids are created for sub-agent
  files, avoiding any `#session_started` collision with the parent.

## 8. Querying sub-agent activity (no schema change)

- All sub-agent events: `SELECT * FROM run_events WHERE raw_payload->>'isSidechain' = 'true'`.
- Per agent type: `GROUP BY raw_payload->>'attributionAgent'`.
- Per agent instance: `raw_payload->>'agentId'`.
- A run's total cost/tokens already includes sub-agent events (same run).

## 9. Testing

Reuses the live Postgres container and `pytest-` cleanup.

- `discovery`: a temp tree with `slug/s1.jsonl` and
  `slug/s1/subagents/agent-x.jsonl` returns both, parents ordered before
  children.
- `cli` (unit): `events_for_file` on a sub-agent file uses the parent session id
  (from the path) and emits **no** `session_started`/`session_ended`.
- e2e: ingest a parent file **and** its sub-agent file; assert both populate a
  **single** run (one `runs` row, shared session id), the sub-agent events are
  present and carry `agentId`/`isSidechain` in `raw_payload`, and a re-run is
  fully idempotent (0 new rows).
- `report`: `sub_agent_files` count reflects the nested files processed.

Fixtures are sanitized synthetic transcripts checked into `capture/tests/`.

## 10. Scope

**In scope (v1):**
- Discover and ingest sub-agent transcripts, merged into the parent run.
- Report sub-agent coverage.

**Out of scope:**
- `parent_run_id`, per-sub-agent runs, any schema migration.
- Agent-type registry / per-agent scoring / insights (future milestones).
- Deeper-than-`subagents` nesting (none exists today; revisit if it appears).

## 11. Assumptions & open questions

- **Assumption:** every nested transcript is `.../<parent>/subagents/agent-*.jsonl`
  and its lines' `sessionId` equals `<parent>`. (Verified across all 135 files.)
- **Assumption:** a sub-agent file's `cwd` matches its parent's, so it resolves
  to the same repository/catch-all. If they ever differ, the sub-agent events
  follow their own `cwd` resolution — acceptable.
- **Open:** whether to later promote agent attribution to columns or a
  `parent_run_id` tree — deferred to the registry/insights milestone, when the
  required query shapes are known.
