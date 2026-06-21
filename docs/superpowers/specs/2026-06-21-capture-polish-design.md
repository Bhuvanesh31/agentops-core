# Capture Polish — Design Spec

**Date:** 2026-06-21
**Status:** Proposed (awaiting review)
**Builds on:** Claude Code capture + sub-agent capture (PR #3)

## 1. Goal

Give captured sessions a real project/repository home instead of the catch-all.
Two deliverables:

1. **`POST /repositories`** — a write endpoint to register a repository, so
   adding repos no longer requires hand-editing `seed.sql`.
2. **Reclassify the 78 `unsorted` sessions** into their real repos, durably —
   so a future full backfill does not re-pollute the catch-all.

**Out of scope (deferred to their own milestones):** OTEL ingestion; a generic
`local_path` resolution layer; `POST /projects`; run delete/merge; the
`runs.started_at` data-quality fix (noted in §10).

## 2. Context & current state

- 106 runs captured: 28 attributed to `agentops-core`, **78 in the catch-all**
  (`project_id='unsorted'`, `repository_id='unsorted-local'`).
- Identity resolves a session by its **canonical git remote** (`identity.py`).
  `run_events` carries no `repository_id`/`project_id` — those live only on
  `runs` (events hang off `run_id`, `ON DELETE CASCADE`), so reclassifying is a
  pure `UPDATE runs`.
- Investigation (see `memory/claude-capture-design-decisions.md`) found the 78
  span 6 historical `cwd`s. The projects were **moved** into
  `~/AI_Native_Workspace`, so the captured (historical) `cwd` ≠ the current
  repo path, and the old folders are deleted or remote-less. Re-extraction
  alone cannot reclassify them — their recorded `cwd` has no resolvable remote.
- All four target repos now have canonical remotes (the `ai-work-journal`
  remote was created 2026-06-21). So **future** sessions resolve via the
  existing remote path; only **historical** data needs new logic.

## 3. The finalized mapping

| Historical cwd(s) | Sessions | → repository_id | → project_id | Canonical remote |
| --- | ---: | --- | --- | --- |
| `~/leadle_master_claude`, `~/AI_Native_Workspace/30-leadle-systems/leadle_gtm_intelligence` | 19 | `leadle-os` | `leadle` | `github.com/Bhuvanesh31/leadle-os` |
| `~/AI_Native_Workspace/30-leadle-systems/leadle_content_studio` | 1 | `leadle-content-studio` | `leadle` | `github.com/Bhuvanesh31/leadle-content-studio` |
| `~/Claude Folders/AI_Native_Plans` | 46 | `ai-native-revops-work-brain` | `ai-native-work-brain` | `github.com/Bhuvanesh31/ai-native-revops-work-brain` |
| `~/Claude Folders/claude_sessions_project` | 11 | `ai-work-journal` | `ai-work-journal` | `github.com/Bhuvanesh31/ai-work-journal` |
| `~/AI_Native_Workspace/30-leadle-systems` (root) | 1 | *(stays `unsorted-local`)* | `unsorted` | — |

Project taxonomy: group by workspace area — `leadle` holds both Leadle repos;
the two `20-products` repos are their own projects. `repository_id`s mirror the
GitHub repo slug (existing `agentops-core-main` is the lone outlier — left
as-is).

## 4. Decisions

- **Mechanism: one shared `cwd → repository_id` override map**, consumed by
  *both* the capture adapter (so future backfills route old transcripts
  correctly) and the reclassify command (so the 78 already-stored runs move).
  Single source of truth; no re-pollution.
- **Reclassify delivery: a host-local maintenance command** that writes via
  `app.database.get_connection()` directly. Reclassifying stored runs is a
  rare admin op; an API endpoint for it is premature (YAGNI). The durable part
  is the adapter map. Promote to an endpoint later only if a remote desktop or
  UI needs it.
- **Reference data (projects + the 4 repos) is seeded in `seed.sql`** for
  reproducibility on a fresh DB (like `agentops-core`). `POST /repositories`
  is the new general-purpose write capability, tested independently with
  `pytest-`-prefixed rows — it is not the only way the 4 repos exist.

## 5. Architecture / components

### 5.1 `POST /repositories` (`app/`)
- **`app/schemas/repositories.py`** — add `RepositoryCreate`:
  `repository_id, project_id, repository_name, remote_url: str|None=None,
  local_path: str|None=None, default_branch: str="main", is_active: bool=True`.
- **`app/models/ingestion.py`** — add `upsert_repository(conn, **fields) ->
  dict`: `INSERT INTO repositories (...) VALUES (...) ON CONFLICT
  (repository_id) DO UPDATE SET ... , updated_at=NOW() RETURNING ...` (same
  upsert contract as `seed.sql`). Reuse existing `get_project(conn,
  project_id)` to validate the FK before writing.
- **`app/routes/repositories.py`** — add
  `POST /repositories` → 201 `RepositoryOut`. If `project_id` does not exist →
  `400` (clear message). Body validation errors → `422` (FastAPI default).
  Re-`POST` of an existing `repository_id` upserts (idempotent), returns 200.

### 5.2 Override map + loader (`capture/claude_code/overrides.py`, new)
- **File:** `capture/claude_code/cwd_overrides.toml`, version-controlled
  (machine-specific absolute paths, like `.env` is environment-specific):
  ```toml
  [overrides]
  "/home/bhuvanesh/leadle_master_claude" = "leadle-os"
  "/home/bhuvanesh/AI_Native_Workspace/30-leadle-systems/leadle_gtm_intelligence" = "leadle-os"
  "/home/bhuvanesh/AI_Native_Workspace/30-leadle-systems/leadle_content_studio" = "leadle-content-studio"
  "/home/bhuvanesh/Claude Folders/AI_Native_Plans" = "ai-native-revops-work-brain"
  "/home/bhuvanesh/Claude Folders/claude_sessions_project" = "ai-work-journal"
  ```
- **`load_overrides(path) -> dict[str, str]`** — parse with `tomllib`
  (stdlib, Python 3.12); return the `overrides` table. Missing file → `{}`.
- **`match_override(cwd, mapping) -> V | None`** — **longest-prefix** match:
  among keys where `cwd == key` or `cwd.startswith(key + "/")`, return the
  *value* of the longest matching key, else `None`. Value type is whatever the
  passed mapping holds — a `repository_id` for the raw map, or a
  `(project_id, repository_id)` tuple for the resolved map (§5.3).
  (Longest-prefix so a child folder under a mapped path resolves, and a more
  specific mapping wins over a broader one.)

### 5.3 Adapter resolution change (`capture/claude_code/identity.py`, `cli.py`)
- Extend `resolve_repository(cwd, registry, overrides=None)`:
  ```python
  canon = canonicalize_remote(get_git_remote(cwd))
  if canon is None:
      if overrides:
          hit = match_override(cwd, overrides)   # (project_id, repository_id) or None
          if hit:
              return RepoResolution("registered", None, hit[0], hit[1])
      return RepoResolution("local_only", None)
  # ... unchanged: registry hit -> registered; else pending
  ```
  The override map alone is `cwd → repository_id`; the **project_id** is looked
  up from the registered repos. `process()` builds a `repository_id ->
  project_id` index from `client.fetch_repositories(...)` and resolves the raw
  override map into `cwd → (project_id, repository_id)` once, before the file
  loop, passing the resolved structure to `resolve_repository`. An override
  whose `repository_id` is not registered is skipped with a report note (never
  a crash).
- **`process()`** loads the override file (default
  `capture/claude_code/cwd_overrides.toml`, overridable via `--cwd-map` /
  `AGENTOPS_CWD_MAP`). Files resolved via the override map count as
  `files_processed` (a real repo), not `files_catch_all`.

### 5.4 Reclassify command (`capture/claude_code/reclassify.py`, new)
- `main(argv)`: load the raw `cwd → repository_id` map; open
  `get_connection()`. For each mapping entry:
  - look up `project_id` from `repositories` (`get_project`-style by
    `repository_id`); if the repo is not registered → skip + warn.
  - `UPDATE runs SET repository_id=%s, project_id=%s, updated_at=NOW()
     WHERE repository_id='unsorted-local'
       AND (cwd = %s OR cwd LIKE %s || '/%')` — report the row count moved.
- Idempotent: a second run moves 0 rows (the first run already changed
  `repository_id` off `unsorted-local`). The `unsorted` root folder is absent
  from the map, so its 1 run is left untouched.
- Guard: the `UNIQUE(tool_id, repository_id, session_id)` key cannot collide
  here (these sessions were never captured under the target repos); if a
  collision ever arose, the `UPDATE` would error loudly — acceptable for a
  host-local admin tool.

### 5.5 Reference data (`database/seed.sql`)
Add idempotent upserts for projects `leadle`, `ai-native-work-brain`,
`ai-work-journal`, and the 4 repositories in §3 (remote_url set, local_path the
current workspace path, `is_active=TRUE`), matching the existing
`ON CONFLICT DO UPDATE` style.

## 6. Data flow

```
seed.sql (3 projects + 4 repos)            # reference data, idempotent
   ↓
POST /repositories                          # general write capability (also future repos)
   ↓
reclassify  (UPDATE 78 → mapped repos)      # one-time, host-local, idempotent
   ↓
future captures:
   cwd has remote  → registry  (as today)
   cwd no remote   → override map → real repo     # old transcripts, no re-pollution
   else            → catch-all / skip
```

## 7. Error handling

- `POST /repositories`: unknown `project_id` → 400; malformed body → 422;
  duplicate `repository_id` → idempotent upsert (200).
- Override map: missing file → empty map (adapter behaves exactly as today);
  entry pointing at an unregistered `repository_id` → skipped + reported.
- Reclassify: no matching runs → 0 moved (not an error); repo not registered →
  skip + warn; never deletes or touches events directly.

## 8. Testing

Live Postgres, `pytest-`-prefixed rows, sanitized synthetic data.

- **`POST /repositories`**: create (201) + row present; missing project → 400;
  idempotent re-POST upserts (no duplicate, fields updated).
- **`overrides`**: `load_overrides` parses the TOML; `match_override` longest-
  prefix (exact match, child-path match, more-specific-wins, no-match → None).
- **adapter (`identity`/`cli`)**: a no-remote `cwd` present in the override map
  resolves to the mapped repo (`registered`), not `local_only`/catch-all; a
  no-remote `cwd` absent from the map still → `local_only`.
- **reclassify**: seed `unsorted-local` runs at mapped cwds (+ one at the
  unmapped root) → command moves only the mapped ones to the right
  `repository_id`/`project_id`; the root run stays `unsorted`; events remain
  attached (join still returns them); a second run moves 0.

## 9. Scope

**In scope (v1):** `POST /repositories`; the shared override map + loader/matcher;
adapter resolution via the map; the reclassify maintenance command; reference
data (projects + 4 repos) in `seed.sql`; README + ADR 0005.

**Out of scope:** OTEL ingestion; generic `local_path`-prefix resolution
(unnecessary now that all four repos have remotes); `POST /projects`; run
delete/merge; multi-desktop sync of the override map.

## 10. Assumptions & open questions

- **Assumption:** the override map's machine-specific paths are acceptable to
  check in (single-desktop today; matches the `seed.sql` `local_path` note that
  paths are host-specific).
- **Assumption:** the 4 repos' canonical remotes are stable (verified
  2026-06-21 via `git remote get-url origin`).
- **Follow-up (not this milestone):** `runs.started_at` reflects ingestion/
  synthesis time, not real session time (real time is on events'
  `occurred_at`); derive it from the earliest event in a later pass.
- **Open:** project/repository_id names are proposed; adjustable at spec review.
