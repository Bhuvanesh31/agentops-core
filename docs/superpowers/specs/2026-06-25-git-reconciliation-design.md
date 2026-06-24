# Git Commit Reconciliation — Design Spec

**Date:** 2026-06-25  
**Status:** Approved  
**AGENTS.md step:** #7  

---

## Goal

Connect captured AI-session runs to the git commits they produced. This enables shipped-outcome metrics (cost-per-feature, time-to-merge) and the git/PR-reconciled differentiation wedge from the north-star vision.

## Architecture

```
Host machine (has git + DB access)
├── capture/git/query.py        NEW — git log → list of commit dicts
├── capture/git/reconcile.py    NEW — reconcile CLI (--repo / --all)
├── capture/git/__init__.py     NEW — empty package marker
└── capture/claude_code/cli.py  MODIFIED — post-capture git step

PostgreSQL commits table         EXISTING — no schema changes needed

API container (no git access)
├── app/routes/reads.py         MODIFIED — GET /runs/{run_id}/commits
├── app/models/reads.py         MODIFIED — list_run_commits()
└── app/schemas/reads.py        MODIFIED — RunCommit Pydantic model

Verification UI
├── app/static/run.html         MODIFIED — Commits section in run detail
└── app/static/app.js           MODIFIED — fetch + render commits
```

The `commits` table already exists in `database/schema.sql` with all required columns. No migration is needed.

---

## Global Constraints

- PostgreSQL is the system of record. Raw SQL via psycopg3. No ORM.
- `cost_usd` stays NULL / `cost_source='unavailable'` — this feature does not touch usage_metrics.
- NULL renders as `—` in UI. No zero-filling.
- No write paths in the UI. `/ui` is read-only.
- Capture flow must never block. Git reconciliation failures are non-fatal and logged but do not prevent event submission.
- Upserts are idempotent: `INSERT ... ON CONFLICT (commit_sha) DO NOTHING`.
- Skip reconciliation for runs where `ended_at IS NULL` — open-ended time window is unreliable.
- Tests run against a real Postgres instance (not mocks). Git fixture tests use a real `git init` in a temp directory.

---

## Data Layer

### commits table (existing)

```sql
CREATE TABLE commits (
    commit_sha   TEXT PRIMARY KEY,
    run_id       UUID REFERENCES runs(run_id) ON DELETE SET NULL,
    repository_id TEXT NOT NULL REFERENCES repositories(repository_id),
    tool_id      TEXT REFERENCES tools(tool_id),
    session_id   TEXT,
    branch       TEXT,
    author_name  TEXT,
    author_email TEXT,
    commit_message TEXT,
    committed_at TIMESTAMPTZ,
    received_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

Existing indexes: `idx_commits_run`, `idx_commits_repository`, `idx_commits_session`.

### Matching logic

Given a run with `started_at`, `ended_at`, `branch`, `cwd`:

- Query window: `after = started_at`, `before = ended_at + 30 minutes`
- Git command:
  ```bash
  git -C <cwd> log <branch> \
    --format="%H|%an|%ae|%ai|%s" \
    --after="<after ISO>" \
    --before="<before ISO>"
  ```
- Each output line → one commit dict: `{commit_sha, author_name, author_email, committed_at, commit_message}`
- Upsert: `INSERT INTO commits (commit_sha, run_id, repository_id, branch, author_name, author_email, commit_message, committed_at) VALUES ... ON CONFLICT (commit_sha) DO NOTHING`

### Ambiguity resolution

When a commit's timestamp falls within multiple runs' windows (same branch, overlapping sessions): the reconcile command processes runs in `ended_at DESC` order (newest first), so the most-recently-completed run that covers a commit is linked first. `ON CONFLICT DO NOTHING` preserves that assignment — no run can steal a commit already linked to a newer run.

---

## Capture Components

### `capture/git/__init__.py`
Empty package marker.

### `capture/git/query.py`

Single public function:

```python
def query_commits(
    cwd: str,
    branch: str,
    after: datetime,
    before: datetime,
) -> list[dict]:
    ...
```

- Runs `git -C cwd log branch --format="%H|%an|%ae|%ai|%s" --after=... --before=...`
- Parses each line into `{commit_sha, author_name, author_email, committed_at, commit_message}`
- Returns `[]` if `cwd` does not exist, git is not available, or git returns a non-zero exit code. Never raises — all errors logged to stderr.

### `capture/git/reconcile.py`

CLI entry point: `python -m capture.git reconcile`

```
Options:
  --repo <repository_id>   Reconcile only runs for this repository
  --all                    Reconcile all repositories (default)
  --dry-run                Print what would be linked; do not write
```

Algorithm:
1. Connect to Postgres via `DATABASE_URL`.
2. Query runs: `SELECT run_id, repository_id, tool_id, session_id, branch, cwd, started_at, ended_at FROM runs WHERE ended_at IS NOT NULL AND branch IS NOT NULL AND cwd IS NOT NULL ORDER BY ended_at DESC`.
3. Filter by `--repo` if provided.
4. For each run, call `query_commits(cwd, branch, started_at, ended_at)`.
5. Upsert each commit into `commits` with `run_id` set.
6. Print per-run summary: `[<run_id_short>] branch=<branch> → <n> commits linked`.
7. Print totals at the end.

### `capture/claude_code/cli.py` (modification)

After all events for a session file are submitted successfully, add a post-capture git step:

1. Connect to Postgres via `DATABASE_URL`.
2. Look up the run: `SELECT run_id, started_at, ended_at, branch, cwd FROM runs WHERE tool_id = %s AND repository_id = %s AND session_id = %s`.
3. If found and `ended_at IS NOT NULL` and `branch IS NOT NULL` and `cwd IS NOT NULL`: call `query_commits` and upsert commits.
4. Any exception → log to stderr, continue. Never raises. Does not affect the exit code.

`DATABASE_URL` is already in `.env` and sourced before running the CLI.

---

## API Changes

### New read endpoint

```
GET /runs/{run_id}/commits
```

- Returns `list[RunCommit]` ordered by `committed_at ASC NULLS LAST`.
- Returns `[]` if the run has no linked commits (not a 404).
- Does not validate that `run_id` exists — caller already fetched the run detail.

### `RunCommit` schema (`app/schemas/reads.py`)

```python
class RunCommit(BaseModel):
    model_config = ConfigDict(extra="ignore")

    commit_sha: str
    branch: str | None = None
    author_name: str | None = None
    author_email: str | None = None
    commit_message: str | None = None
    committed_at: datetime | None = None
```

### `list_run_commits()` (`app/models/reads.py`)

```python
def list_run_commits(conn: psycopg.Connection, run_id: str) -> list[dict]:
    return conn.execute(
        """
        SELECT commit_sha, branch, author_name, author_email,
               commit_message, committed_at
        FROM commits
        WHERE run_id = %s
        ORDER BY committed_at ASC NULLS LAST
        """,
        (run_id,),
    ).fetchall()
```

---

## UI Changes

### `app/static/run.html`

Add a "Commits" section below the Events section in the run detail view:

```html
<section id="commits-section">
  <h2>Commits</h2>
  <table id="commits-table">
    <thead>
      <tr>
        <th>SHA</th>
        <th>Message</th>
        <th>Author</th>
        <th>Committed at</th>
      </tr>
    </thead>
    <tbody id="commits-tbody"></tbody>
  </table>
  <p id="commits-empty" hidden>No commits linked</p>
</section>
```

### `app/static/app.js`

In the run detail fetch flow, after fetching run events:

```javascript
async function loadRunCommits(runId) {
  const commits = await fetch(`/runs/${runId}/commits`).then(r => r.json());
  const tbody = document.getElementById("commits-tbody");
  const empty = document.getElementById("commits-empty");
  if (!commits.length) {
    empty.hidden = false;
    return;
  }
  commits.forEach(c => {
    const tr = document.createElement("tr");
    const cells = [
      c.commit_sha.slice(0, 7),
      c.commit_message ?? "—",
      c.author_name ?? "—",
      c.committed_at ? new Date(c.committed_at).toLocaleString() : "—",
    ];
    cells.forEach((text, i) => {
      const td = document.createElement("td");
      if (i === 0) {
        const code = document.createElement("code");
        code.textContent = text;
        td.appendChild(code);
      } else {
        td.textContent = text;
      }
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
}
```

All values set via `textContent` — never `innerHTML`. `commit_message` and `author_name` are raw strings from git and must not be rendered as HTML.

---

## Testing

### `tests/test_git_reconcile.py`

**Git fixture**: `pytest` fixture that creates a real `git init` repo in a temp directory, sets `user.name` and `user.email`, creates one or more commits at controlled timestamps.

**Tests:**
1. `test_query_commits_returns_commits` — fixture repo with 2 commits in window; `query_commits` returns both.
2. `test_query_commits_empty_outside_window` — commits outside the time window return `[]`.
3. `test_query_commits_bad_cwd_returns_empty` — non-existent cwd returns `[]`, no exception.
4. `test_reconcile_upsert_idempotent` — upsert the same commit twice; DB has exactly one row.
5. `test_reconcile_conflict_do_nothing` — commit already linked to run A; reconciling run B with overlapping window does not reassign.
6. `test_get_run_commits_endpoint` — insert two commits into DB for a run; `GET /runs/{id}/commits` returns both ordered by `committed_at`.
7. `test_get_run_commits_empty` — run with no commits returns `[]`.

---

## Files Changed Summary

| File | Change |
|---|---|
| `capture/git/__init__.py` | CREATE — package marker |
| `capture/git/query.py` | CREATE — `query_commits()` |
| `capture/git/reconcile.py` | CREATE — reconcile CLI |
| `capture/claude_code/cli.py` | MODIFY — post-capture git step |
| `app/routes/reads.py` | MODIFY — `GET /runs/{run_id}/commits` |
| `app/models/reads.py` | MODIFY — `list_run_commits()` |
| `app/schemas/reads.py` | MODIFY — `RunCommit` model |
| `app/static/run.html` | MODIFY — Commits section |
| `app/static/app.js` | MODIFY — `loadRunCommits()` |
| `tests/test_git_reconcile.py` | CREATE — 7 tests |

---

## Out of Scope

- GitHub/GitLab PR links — requires parsing remote URL; deferred.
- Commit diffs or file-level attribution — deferred.
- Automatic periodic reconciliation (cron) — run manually or at capture time.
- Codex capture — separate milestone.
