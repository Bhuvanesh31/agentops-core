# Git Commit Reconciliation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect captured AI-session runs to the git commits they produced by querying `git log` and linking results into the existing `commits` table.

**Architecture:** A new `capture/git/` module provides a pure `query_commits()` function and a `reconcile_runs()` function that upserts commits into Postgres. The existing Claude Code capture CLI gains a post-capture reconcile step. A new `GET /runs/{run_id}/commits` read endpoint and a Commits section in the run detail UI complete the feature.

**Tech Stack:** Python 3.11, psycopg3, subprocess (git), FastAPI, Vanilla JS

## Global Constraints

- PostgreSQL is the system of record. Raw parameterized SQL via psycopg3. No ORM.
- The `commits` table already exists in `database/schema.sql` — no schema migration needed.
- `upsert_commits` uses `ON CONFLICT (commit_sha) DO NOTHING` — idempotent by design.
- Skip reconciliation for any run where `ended_at IS NULL`, `branch IS NULL`, or `cwd IS NULL`.
- Commit matching window: `[run.started_at, run.ended_at + 30 minutes]`.
- Reconcile command processes runs in `ended_at DESC` order (newest first) so the most-recently-completed run wins when a commit falls in two windows.
- Git reconciliation failures are non-fatal: log to stderr, never raise, never block capture.
- NULL renders as `—` in UI. Never coerce to zero or empty string.
- No write paths in the UI. `/ui` is read-only.
- All captured data rendered via `textContent`, never `innerHTML`.
- Tests run against the live Postgres container (same as every other test in this repo).
- Test suite command: `set -a; . ./.env; set +a; .venv/bin/python -m pytest -q`

---

### Task 1: `capture/git/query.py` — git log query function

**Files:**
- Create: `capture/git/__init__.py`
- Create: `capture/git/query.py`
- Modify: `tests/conftest.py` (add commit cleanup before run deletion)
- Create: `tests/test_git_query.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `query_commits(cwd: str, branch: str, after: datetime, before: datetime) -> list[dict]`
  Each dict has keys: `commit_sha` (str), `author_name` (str | None), `author_email` (str | None), `committed_at` (datetime | None), `commit_message` (str | None).

- [ ] **Step 1: Create the package marker**

Create `capture/git/__init__.py` — empty file.

```bash
touch capture/git/__init__.py
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_git_query.py`:

```python
"""Tests for capture.git.query — git log wrapper."""

import os
import subprocess
from datetime import datetime, timezone

import pytest

from capture.git.query import query_commits


@pytest.fixture
def git_repo(tmp_path):
    """Real git repo with 2 commits at controlled timestamps."""
    repo = tmp_path / "repo"
    repo.mkdir()

    def run(*args, **extra_env):
        env = {**os.environ, **extra_env}
        subprocess.run(list(args), cwd=str(repo), check=True, env=env, capture_output=True)

    run("git", "init")
    run("git", "config", "user.email", "t@t.com")
    run("git", "config", "user.name", "Tester")
    run("git", "checkout", "-b", "main")

    (repo / "a.txt").write_text("a")
    run("git", "add", ".")
    run(
        "git", "commit", "-m", "first commit",
        GIT_AUTHOR_DATE="2026-06-25T10:00:00+00:00",
        GIT_COMMITTER_DATE="2026-06-25T10:00:00+00:00",
    )

    (repo / "b.txt").write_text("b")
    run("git", "add", ".")
    run(
        "git", "commit", "-m", "second commit",
        GIT_AUTHOR_DATE="2026-06-25T10:30:00+00:00",
        GIT_COMMITTER_DATE="2026-06-25T10:30:00+00:00",
    )

    return repo


def test_query_commits_returns_all_in_window(git_repo):
    after = datetime(2026, 6, 25, 9, 0, tzinfo=timezone.utc)
    before = datetime(2026, 6, 25, 11, 0, tzinfo=timezone.utc)
    commits = query_commits(str(git_repo), "main", after, before)
    assert len(commits) == 2
    messages = {c["commit_message"] for c in commits}
    assert messages == {"first commit", "second commit"}
    sample = commits[0]
    assert sample["author_name"] == "Tester"
    assert sample["author_email"] == "t@t.com"
    assert sample["committed_at"] is not None
    assert len(sample["commit_sha"]) == 40


def test_query_commits_window_excludes_later_commit(git_repo):
    # Window ends at 10:15 — only first commit (10:00) is inside.
    after = datetime(2026, 6, 25, 9, 0, tzinfo=timezone.utc)
    before = datetime(2026, 6, 25, 10, 15, tzinfo=timezone.utc)
    commits = query_commits(str(git_repo), "main", after, before)
    assert len(commits) == 1
    assert commits[0]["commit_message"] == "first commit"


def test_query_commits_nonexistent_cwd_returns_empty():
    after = datetime(2026, 6, 25, 9, 0, tzinfo=timezone.utc)
    before = datetime(2026, 6, 25, 11, 0, tzinfo=timezone.utc)
    result = query_commits("/nonexistent/path/xyz", "main", after, before)
    assert result == []


def test_query_commits_nonexistent_branch_returns_empty(git_repo):
    after = datetime(2026, 6, 25, 9, 0, tzinfo=timezone.utc)
    before = datetime(2026, 6, 25, 11, 0, tzinfo=timezone.utc)
    result = query_commits(str(git_repo), "no-such-branch", after, before)
    assert result == []
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
set -a; . ./.env; set +a
.venv/bin/python -m pytest tests/test_git_query.py -v
```

Expected: `ModuleNotFoundError: No module named 'capture.git.query'` (or similar import error for all 4 tests).

- [ ] **Step 4: Implement `capture/git/query.py`**

```python
"""Query git log for commits within a time window."""

import subprocess
import sys
from datetime import datetime


def query_commits(
    cwd: str,
    branch: str,
    after: datetime,
    before: datetime,
) -> list[dict]:
    """Return commits on branch whose author date falls in (after, before].

    Uses ``git log`` in the given working directory. Returns an empty list on
    any error (missing cwd, git not found, non-zero exit, parse failure) and
    logs the reason to stderr. Never raises.
    """
    try:
        result = subprocess.run(
            [
                "git", "-C", cwd, "log", branch,
                "--format=%H|%an|%ae|%ai|%s",
                f"--after={after.isoformat()}",
                f"--before={before.isoformat()}",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        print(f"[git-reconcile] git error in {cwd!r}: {exc}", file=sys.stderr)
        return []

    if result.returncode != 0:
        print(
            f"[git-reconcile] git log failed in {cwd!r}: {result.stderr.strip()}",
            file=sys.stderr,
        )
        return []

    commits = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("|", 4)
        if len(parts) < 5:
            continue
        sha, author_name, author_email, date_str, message = parts
        try:
            committed_at: datetime | None = datetime.fromisoformat(date_str.strip())
        except ValueError:
            committed_at = None
        commits.append(
            {
                "commit_sha": sha.strip(),
                "author_name": author_name.strip() or None,
                "author_email": author_email.strip() or None,
                "committed_at": committed_at,
                "commit_message": message.strip() or None,
            }
        )
    return commits
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
set -a; . ./.env; set +a
.venv/bin/python -m pytest tests/test_git_query.py -v
```

Expected: 4 tests PASS.

- [ ] **Step 6: Update `tests/conftest.py` to clean up commits**

The `commits` table has `run_id FK ON DELETE SET NULL` (not CASCADE), so deleting runs leaves orphan commit rows. Clean them up before deleting runs.

Find the `cleanup_pytest_rows` fixture in `tests/conftest.py`. It currently looks like:

```python
@pytest.fixture(autouse=True)
def cleanup_pytest_rows() -> Iterator[None]:
    """Remove rows created by tests after each test."""
    yield
    try:
        with get_connection() as conn:
            # Deleting runs cascades to their run_events.
            conn.execute("DELETE FROM runs WHERE session_id LIKE 'pytest-%'")
            conn.execute("DELETE FROM run_events WHERE source_event_id LIKE 'pytest-%'")
            conn.execute("DELETE FROM repositories WHERE repository_id LIKE 'pytest-%'")
            conn.execute("DELETE FROM projects WHERE project_id LIKE 'pytest-%'")
    except Exception:
        # Cleanup is best-effort; never fail a test run on teardown.
        pass
```

Add the commit cleanup line **before** the run deletion (order matters — commits reference run_ids that we need to look up before deleting):

```python
@pytest.fixture(autouse=True)
def cleanup_pytest_rows() -> Iterator[None]:
    """Remove rows created by tests after each test."""
    yield
    try:
        with get_connection() as conn:
            # Delete commits before runs: commits.run_id is SET NULL on run
            # deletion, so we must clean them up first while the FK is still set.
            conn.execute(
                "DELETE FROM commits WHERE run_id IN "
                "(SELECT run_id FROM runs WHERE session_id LIKE 'pytest-%')"
            )
            # Deleting runs cascades to their run_events.
            conn.execute("DELETE FROM runs WHERE session_id LIKE 'pytest-%'")
            conn.execute("DELETE FROM run_events WHERE source_event_id LIKE 'pytest-%'")
            conn.execute("DELETE FROM repositories WHERE repository_id LIKE 'pytest-%'")
            conn.execute("DELETE FROM projects WHERE project_id LIKE 'pytest-%'")
    except Exception:
        # Cleanup is best-effort; never fail a test run on teardown.
        pass
```

- [ ] **Step 7: Run the full suite to confirm no regressions**

```bash
set -a; . ./.env; set +a
.venv/bin/python -m pytest -q
```

Expected: all existing tests + 4 new ones pass.

- [ ] **Step 8: Commit**

```bash
git add capture/git/__init__.py capture/git/query.py \
        tests/test_git_query.py tests/conftest.py
git commit -m "feat: add git log query function + commit cleanup in conftest"
```

---

### Task 2: `capture/git/reconcile.py` — upsert logic + reconcile CLI

**Files:**
- Create: `capture/git/reconcile.py`
- Create: `tests/test_git_reconcile.py`

**Interfaces:**
- Consumes: `query_commits` from `capture.git.query` (Task 1).
- Produces:
  - `upsert_commits(conn: psycopg.Connection, run_id, repository_id: str, branch: str, commits: list[dict]) -> int`
    Upserts commits for one run. Returns the count of rows actually inserted (0 for conflicts).
  - `reconcile_runs(conn: psycopg.Connection, repository_id: str | None = None, dry_run: bool = False) -> dict`
    Iterates all eligible runs (newest-first), queries git, upserts commits. Returns `{"runs_processed": int, "commits_linked": int}`.
  - CLI: `python -m capture.git.reconcile [--repo <id>] [--all] [--dry-run]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_git_reconcile.py`:

```python
"""Tests for capture.git.reconcile — upsert logic."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.database import get_connection
from capture.git.reconcile import upsert_commits
from tests.conftest import SEED_REPOSITORY_ID, SEED_TOOL_ID, make_event


def _get_run_id(session_id: str) -> str:
    """Look up run_id in the DB for a given session_id."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT run_id FROM runs WHERE session_id = %s", (session_id,)
        ).fetchone()
    assert row is not None, f"No run for session {session_id}"
    return str(row["run_id"])


def _make_fake_commit(sha_suffix: str | None = None) -> dict:
    sha = f"pytest-sha-{sha_suffix or uuid4().hex[:16]}"
    return {
        "commit_sha": sha,
        "author_name": "Test Author",
        "author_email": "test@example.com",
        "commit_message": "test commit",
        "committed_at": datetime(2026, 6, 25, 10, 0, tzinfo=timezone.utc),
    }


def test_upsert_commits_inserts_new(client):
    session_id = f"pytest-session-{uuid4().hex}"
    client.post("/runs/events", json=make_event(session_id=session_id))
    run_id = _get_run_id(session_id)
    commit = _make_fake_commit()

    with get_connection() as conn:
        count = upsert_commits(conn, run_id, SEED_REPOSITORY_ID, "main", [commit])

    assert count == 1

    # Verify row exists in DB
    with get_connection() as conn:
        row = conn.execute(
            "SELECT commit_message FROM commits WHERE commit_sha = %s",
            (commit["commit_sha"],),
        ).fetchone()
    assert row is not None
    assert row["commit_message"] == "test commit"


def test_upsert_commits_idempotent(client):
    session_id = f"pytest-session-{uuid4().hex}"
    client.post("/runs/events", json=make_event(session_id=session_id))
    run_id = _get_run_id(session_id)
    commit = _make_fake_commit()

    with get_connection() as conn:
        count1 = upsert_commits(conn, run_id, SEED_REPOSITORY_ID, "main", [commit])
        count2 = upsert_commits(conn, run_id, SEED_REPOSITORY_ID, "main", [commit])

    assert count1 == 1
    assert count2 == 0  # ON CONFLICT DO NOTHING


def test_upsert_commits_conflict_preserves_first_run(client):
    """When two runs cover the same commit, the first-linked run keeps it."""
    session_a = f"pytest-session-{uuid4().hex}"
    session_b = f"pytest-session-{uuid4().hex}"
    client.post("/runs/events", json=make_event(session_id=session_a))
    client.post("/runs/events", json=make_event(session_id=session_b))
    run_id_a = _get_run_id(session_a)
    run_id_b = _get_run_id(session_b)
    commit = _make_fake_commit()

    with get_connection() as conn:
        upsert_commits(conn, run_id_a, SEED_REPOSITORY_ID, "main", [commit])
        upsert_commits(conn, run_id_b, SEED_REPOSITORY_ID, "main", [commit])
        row = conn.execute(
            "SELECT run_id FROM commits WHERE commit_sha = %s",
            (commit["commit_sha"],),
        ).fetchone()

    assert str(row["run_id"]) == run_id_a
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
set -a; . ./.env; set +a
.venv/bin/python -m pytest tests/test_git_reconcile.py -v
```

Expected: `ImportError` — `capture.git.reconcile` does not exist yet.

- [ ] **Step 3: Implement `capture/git/reconcile.py`**

```python
"""Reconcile git commits with captured runs.

Usage:
  python -m capture.git.reconcile --all
  python -m capture.git.reconcile --repo <repository_id>
  python -m capture.git.reconcile --repo <repository_id> --dry-run
"""

import argparse
import os
import sys
from datetime import timedelta

import psycopg
from psycopg.rows import dict_row

from capture.git.query import query_commits


def upsert_commits(
    conn: psycopg.Connection,
    run_id: object,
    repository_id: str,
    branch: str,
    commits: list[dict],
) -> int:
    """Insert commits for a run. ON CONFLICT (commit_sha) DO NOTHING.

    Returns the count of rows actually inserted (0 for each conflict).
    """
    inserted = 0
    for c in commits:
        cursor = conn.execute(
            """
            INSERT INTO commits
                (commit_sha, run_id, repository_id, branch,
                 author_name, author_email, commit_message, committed_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (commit_sha) DO NOTHING
            """,
            (
                c["commit_sha"],
                run_id,
                repository_id,
                branch,
                c.get("author_name"),
                c.get("author_email"),
                c.get("commit_message"),
                c.get("committed_at"),
            ),
        )
        inserted += cursor.rowcount
    return inserted


def reconcile_runs(
    conn: psycopg.Connection,
    repository_id: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Query git log for each eligible run and upsert commits.

    Processes runs newest-ended-first so the most-recently-completed run wins
    when a commit falls within two overlapping windows (ON CONFLICT DO NOTHING
    preserves the first-inserted link).

    Returns {"runs_processed": int, "commits_linked": int}.
    """
    sql = """
        SELECT run_id, repository_id, branch, cwd, started_at, ended_at
        FROM runs
        WHERE ended_at IS NOT NULL
          AND branch IS NOT NULL
          AND cwd IS NOT NULL
    """
    params: list = []
    if repository_id is not None:
        sql += " AND repository_id = %s"
        params.append(repository_id)
    sql += " ORDER BY ended_at DESC"

    rows = conn.execute(sql, params).fetchall()

    runs_processed = 0
    commits_linked = 0

    for row in rows:
        before = row["ended_at"] + timedelta(minutes=30)
        commits = query_commits(
            row["cwd"], row["branch"], row["started_at"], before
        )
        if not commits:
            continue

        runs_processed += 1
        if not dry_run:
            n = upsert_commits(
                conn, row["run_id"], row["repository_id"], row["branch"], commits
            )
            commits_linked += n
        else:
            commits_linked += len(commits)

        run_short = str(row["run_id"])[:8]
        prefix = "[dry-run] " if dry_run else ""
        print(
            f"{prefix}[{run_short}] branch={row['branch']} "
            f"cwd={row['cwd']} → {len(commits)} commits"
        )

    return {"runs_processed": runs_processed, "commits_linked": commits_linked}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reconcile git commits with AgentOps captured runs"
    )
    parser.add_argument(
        "--repo",
        default=None,
        metavar="REPOSITORY_ID",
        help="Reconcile only runs for this repository (default: all)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Reconcile all repositories (default when --repo is omitted)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be linked; do not write to the database",
    )
    args = parser.parse_args(argv)

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL is not set", file=sys.stderr)
        return 1

    with psycopg.connect(db_url, row_factory=dict_row) as conn:
        result = reconcile_runs(conn, repository_id=args.repo, dry_run=args.dry_run)

    print(
        f"\nDone. runs_processed={result['runs_processed']} "
        f"commits_linked={result['commits_linked']}"
        + (" (dry-run)" if args.dry_run else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
set -a; . ./.env; set +a
.venv/bin/python -m pytest tests/test_git_reconcile.py -v
```

Expected: 3 tests PASS.

- [ ] **Step 5: Run the full suite to confirm no regressions**

```bash
set -a; . ./.env; set +a
.venv/bin/python -m pytest -q
```

Expected: all tests pass.

- [ ] **Step 6: Smoke test the CLI (dry-run)**

```bash
set -a; . ./.env; set +a
python -m capture.git.reconcile --all --dry-run
```

Expected: prints run lines for runs that have branch/cwd/ended_at, ends with "Done. runs_processed=N commits_linked=M (dry-run)". No DB writes. Exit code 0.

- [ ] **Step 7: Commit**

```bash
git add capture/git/reconcile.py tests/test_git_reconcile.py
git commit -m "feat: add git commit reconcile logic and CLI"
```

---

### Task 3: Capture-time git step in `capture/claude_code/cli.py`

**Files:**
- Modify: `capture/claude_code/cli.py`

**Interfaces:**
- Consumes: `reconcile_runs` from `capture.git.reconcile` (Task 2); `DATABASE_URL` env var.
- Produces: no new public interface. The `main()` function gains a post-capture reconcile step when `DATABASE_URL` is set and `--dry-run` is not passed.

- [ ] **Step 1: Add the post-capture reconcile step to `capture/claude_code/cli.py`**

The `main()` function currently ends with:

```python
    files = discover_transcripts(args.projects_dir, only=args.only, since=args.since)
    with httpx.Client() as http:
        report = process(
            http,
            args.api_url,
            files,
            dry_run=args.dry_run,
            catch_all=args.catch_all,
            exclude=args.exclude,
            cwd_overrides=load_overrides(args.cwd_map),
        )
    print(report.render())
    return 0
```

Replace it with:

```python
    files = discover_transcripts(args.projects_dir, only=args.only, since=args.since)
    with httpx.Client() as http:
        report = process(
            http,
            args.api_url,
            files,
            dry_run=args.dry_run,
            catch_all=args.catch_all,
            exclude=args.exclude,
            cwd_overrides=load_overrides(args.cwd_map),
        )
    print(report.render())

    if not args.dry_run:
        _reconcile_after_capture()

    return 0
```

Add the helper function anywhere in the file before `main()`:

```python
def _reconcile_after_capture() -> None:
    """Post-capture: link git commits to runs just ingested.

    Requires DATABASE_URL in the environment (already needed by maintenance
    commands). Skips silently if not set. All errors are logged to stderr and
    swallowed — this step must never block or fail the capture flow.
    """
    import os

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        return

    try:
        import psycopg
        from psycopg.rows import dict_row

        from capture.git.reconcile import reconcile_runs

        with psycopg.connect(db_url, row_factory=dict_row) as conn:
            result = reconcile_runs(conn)

        if result["commits_linked"] > 0:
            print(
                f"[git-reconcile] linked {result['commits_linked']} commits "
                f"across {result['runs_processed']} runs"
            )
    except Exception as exc:  # noqa: BLE001
        import sys

        print(f"[git-reconcile] post-capture reconcile failed: {exc}", file=sys.stderr)
```

- [ ] **Step 2: Run the full suite to confirm no regressions**

```bash
set -a; . ./.env; set +a
.venv/bin/python -m pytest -q
```

Expected: all tests pass. The reconcile step runs after each test that invokes `main()`, but since test transcripts have no git repos at their `cwd` values, `query_commits` returns empty lists and nothing is written.

- [ ] **Step 3: Smoke test — dry run still skips reconcile**

```bash
set -a; . ./.env; set +a
python -m capture.claude_code.cli --dry-run --projects-dir ~/.claude/projects
```

Expected: runs the normal dry-run output; no `[git-reconcile]` lines (dry-run skips the reconcile step).

- [ ] **Step 4: Commit**

```bash
git add capture/claude_code/cli.py
git commit -m "feat: add post-capture git commit reconcile step to capture CLI"
```

---

### Task 4: `GET /runs/{run_id}/commits` read API

**Files:**
- Modify: `app/schemas/reads.py` (add `RunCommit`)
- Modify: `app/models/reads.py` (add `list_run_commits`)
- Modify: `app/routes/reads.py` (add `GET /runs/{run_id}/commits`)
- Create: `tests/test_reads_commits.py`

**Interfaces:**
- Consumes: `upsert_commits` from `capture.git.reconcile` (Task 2) — used in tests to seed data.
- Produces: `GET /runs/{run_id}/commits → list[RunCommit]`, ordered `committed_at ASC NULLS LAST`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_reads_commits.py`:

```python
"""Tests for GET /runs/{run_id}/commits."""

from datetime import datetime, timezone
from uuid import uuid4

from app.database import get_connection
from capture.git.reconcile import upsert_commits
from tests.conftest import SEED_REPOSITORY_ID, make_event


def _get_run_id(session_id: str) -> str:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT run_id FROM runs WHERE session_id = %s", (session_id,)
        ).fetchone()
    assert row is not None
    return str(row["run_id"])


def test_get_run_commits_returns_linked_commits(client):
    session_id = f"pytest-session-{uuid4().hex}"
    client.post("/runs/events", json=make_event(session_id=session_id))
    run_id = _get_run_id(session_id)

    commits = [
        {
            "commit_sha": f"pytest-sha-{uuid4().hex[:16]}",
            "author_name": "Alice",
            "author_email": "alice@example.com",
            "commit_message": "add feature",
            "committed_at": datetime(2026, 6, 25, 10, 0, tzinfo=timezone.utc),
        },
        {
            "commit_sha": f"pytest-sha-{uuid4().hex[:16]}",
            "author_name": "Alice",
            "author_email": "alice@example.com",
            "commit_message": "fix tests",
            "committed_at": datetime(2026, 6, 25, 10, 5, tzinfo=timezone.utc),
        },
    ]
    with get_connection() as conn:
        upsert_commits(conn, run_id, SEED_REPOSITORY_ID, "main", commits)

    resp = client.get(f"/runs/{run_id}/commits")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    # Ordered by committed_at ASC
    assert data[0]["commit_message"] == "add feature"
    assert data[1]["commit_message"] == "fix tests"
    assert data[0]["author_name"] == "Alice"
    assert "commit_sha" in data[0]


def test_get_run_commits_returns_empty_list_when_none(client):
    session_id = f"pytest-session-{uuid4().hex}"
    client.post("/runs/events", json=make_event(session_id=session_id))
    run_id = _get_run_id(session_id)

    resp = client.get(f"/runs/{run_id}/commits")
    assert resp.status_code == 200
    assert resp.json() == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
set -a; . ./.env; set +a
.venv/bin/python -m pytest tests/test_reads_commits.py -v
```

Expected: 404 for the `/runs/{id}/commits` endpoint — route does not exist yet.

- [ ] **Step 3: Add `RunCommit` to `app/schemas/reads.py`**

The file currently imports `datetime` and `Decimal` from the stdlib. At the end of the file, add:

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

Also add `RunCommit` to the imports in `app/routes/reads.py` (next step).

- [ ] **Step 4: Add `list_run_commits` to `app/models/reads.py`**

At the end of `app/models/reads.py`, add:

```python
def list_run_commits(conn: psycopg.Connection, run_id: str) -> list[dict[str, Any]]:
    """Return commits linked to a run, oldest-first. Empty list if none."""
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

- [ ] **Step 5: Add the route to `app/routes/reads.py`**

Current import line:
```python
from app.schemas.reads import ProjectOverview, RunDetail, RunEvent, RunListItem
```

Change to:
```python
from app.schemas.reads import ProjectOverview, RunCommit, RunDetail, RunEvent, RunListItem
```

After the existing `get_run_events` route, add:

```python
@router.get("/runs/{run_id}/commits", response_model=list[RunCommit])
def get_run_commits(
    run_id: str, conn: psycopg.Connection = Depends(db_dependency)
) -> list[dict]:
    return reads.list_run_commits(conn, run_id)
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
set -a; . ./.env; set +a
.venv/bin/python -m pytest tests/test_reads_commits.py -v
```

Expected: 2 tests PASS.

- [ ] **Step 7: Run the full suite**

```bash
set -a; . ./.env; set +a
.venv/bin/python -m pytest -q
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add app/schemas/reads.py app/models/reads.py app/routes/reads.py \
        tests/test_reads_commits.py
git commit -m "feat: add GET /runs/{run_id}/commits read endpoint"
```

---

### Task 5: Commits section in run detail UI

**Files:**
- Modify: `app/static/run.html`
- Modify: `app/static/app.js`
- Modify: `tests/test_reads_events.py` (add 2 static-serve tests)

**Interfaces:**
- Consumes: `GET /runs/{run_id}/commits` (Task 4); existing `fetchJSON`, `showError`, `cell` helpers.
- Produces: `renderCommits(container, commits)` function; Commits section rendered below Events in run detail.

- [ ] **Step 1: Write the failing static-serve tests**

Open `tests/test_reads_events.py` and append at the end:

```python
def test_ui_run_html_has_commits_section(client):
    resp = client.get("/ui/run.html")
    assert resp.status_code == 200
    assert "commits" in resp.text


def test_ui_app_js_has_render_commits(client):
    resp = client.get("/ui/app.js")
    assert resp.status_code == 200
    assert "renderCommits" in resp.text
```

- [ ] **Step 2: Run those tests to verify they fail**

```bash
set -a; . ./.env; set +a
.venv/bin/python -m pytest tests/test_reads_events.py::test_ui_run_html_has_commits_section \
    tests/test_reads_events.py::test_ui_app_js_has_render_commits -v
```

Expected: both FAIL — `commits` not in `run.html`, `renderCommits` not in `app.js`.

- [ ] **Step 3: Update `app/static/run.html`**

The file currently has:

```html
  <section>
    <h2>Events</h2>
    <div id="events">Loading…</div>
  </section>
  <script src="app.js"></script>
  <script>initRun();</script>
```

Replace with:

```html
  <section>
    <h2>Events</h2>
    <div id="events">Loading…</div>
  </section>
  <section>
    <h2>Commits</h2>
    <div id="commits">Loading…</div>
  </section>
  <script src="app.js"></script>
  <script>initRun();</script>
```

- [ ] **Step 4: Update `app/static/app.js`**

Add the `renderCommits` function after `renderEvents`. Insert before `async function initRun()`:

```javascript
function renderCommits(container, commits) {
  container.innerHTML = "";
  if (commits.length === 0) {
    const p = document.createElement("p");
    p.textContent = "No commits linked.";
    container.appendChild(p);
    return;
  }
  const table = document.createElement("table");
  const headRow = table.createTHead().insertRow();
  for (const label of ["SHA", "Message", "Author", "Committed at"]) {
    const th = document.createElement("th");
    th.textContent = label;
    headRow.appendChild(th);
  }
  const body = table.createTBody();
  for (const c of commits) {
    const tr = body.insertRow();
    const values = [
      c.commit_sha ? c.commit_sha.slice(0, 7) : "—",
      c.commit_message ?? "—",
      c.author_name ?? "—",
      c.committed_at ? new Date(c.committed_at).toLocaleString() : "—",
    ];
    values.forEach((text, i) => {
      const td = tr.insertCell();
      if (i === 0) {
        const code = document.createElement("code");
        code.textContent = text;
        td.appendChild(code);
      } else {
        td.textContent = text;  // never innerHTML — commit data is untrusted
      }
    });
  }
  container.appendChild(table);
}
```

Update `initRun()` — the current function ends with:

```javascript
  try {
    const events = await fetchJSON(`/runs/${encodeURIComponent(id)}/events`);
    renderEvents(eventsEl, events);
  } catch (err) {
    showError(eventsEl, err);
  }
}
```

Change to:

```javascript
  try {
    const events = await fetchJSON(`/runs/${encodeURIComponent(id)}/events`);
    renderEvents(eventsEl, events);
  } catch (err) {
    showError(eventsEl, err);
  }
  try {
    const commits = await fetchJSON(`/runs/${encodeURIComponent(id)}/commits`);
    renderCommits(commitsEl, commits);
  } catch (err) {
    showError(commitsEl, err);
  }
}
```

Note: `commitsEl` is declared at the top of `initRun()` (next change) — do not redeclare it here.

Add `commitsEl` lookup near the top of `initRun()`. The current start of `initRun()` is:

```javascript
async function initRun() {
  const id = new URLSearchParams(window.location.search).get("id");
  const detailEl = document.getElementById("detail");
  const eventsEl = document.getElementById("events");
  if (!id) {
```

Change to:

```javascript
async function initRun() {
  const id = new URLSearchParams(window.location.search).get("id");
  const detailEl = document.getElementById("detail");
  const eventsEl = document.getElementById("events");
  const commitsEl = document.getElementById("commits");
  if (!id) {
```

And update the early-exit error path. The current:

```javascript
  if (!id) {
    showError(detailEl, new Error("missing ?id= in URL"));
    return;
  }
```

Stays unchanged — `commitsEl` is declared but not used in the error path, which is fine.

- [ ] **Step 5: Run the new static-serve tests to verify they pass**

```bash
set -a; . ./.env; set +a
.venv/bin/python -m pytest tests/test_reads_events.py::test_ui_run_html_has_commits_section \
    tests/test_reads_events.py::test_ui_app_js_has_render_commits -v
```

Expected: both PASS.

- [ ] **Step 6: Run the full suite**

```bash
set -a; . ./.env; set +a
.venv/bin/python -m pytest -q
```

Expected: all tests pass.

- [ ] **Step 7: Rebuild the container and verify the commits section is served**

```bash
docker compose up -d --build api
sleep 5
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/ui/run.html
```

Expected: `200`.

Open a run detail page in the browser (`http://localhost:8000/ui/run.html?id=<any-run-id>`). A "Commits" section should appear below Events. If no commits have been reconciled yet, it shows "No commits linked."

To populate commits for a run, run:

```bash
set -a; . ./.env; set +a
python -m capture.git.reconcile --all
```

Then reload the run detail page. Runs where `cwd` points to a real local git repo and `branch` matches will show commits.

- [ ] **Step 8: Commit**

```bash
git add app/static/run.html app/static/app.js tests/test_reads_events.py
git commit -m "feat: add commits section to run detail UI"
```

---

## Files Changed Summary

| File | Task | Change |
|---|---|---|
| `capture/git/__init__.py` | 1 | CREATE |
| `capture/git/query.py` | 1 | CREATE |
| `tests/conftest.py` | 1 | MODIFY — commit cleanup before run deletion |
| `tests/test_git_query.py` | 1 | CREATE |
| `capture/git/reconcile.py` | 2 | CREATE |
| `tests/test_git_reconcile.py` | 2 | CREATE |
| `capture/claude_code/cli.py` | 3 | MODIFY — post-capture reconcile step |
| `app/schemas/reads.py` | 4 | MODIFY — `RunCommit` model |
| `app/models/reads.py` | 4 | MODIFY — `list_run_commits()` |
| `app/routes/reads.py` | 4 | MODIFY — `GET /runs/{run_id}/commits` |
| `tests/test_reads_commits.py` | 4 | CREATE |
| `app/static/run.html` | 5 | MODIFY — Commits section |
| `app/static/app.js` | 5 | MODIFY — `renderCommits()` + `initRun()` wiring |
| `tests/test_reads_events.py` | 5 | MODIFY — 2 static-serve tests |
