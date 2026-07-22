"""Tests for capture.git.reconcile — upsert logic and skip classification."""

import os
import subprocess
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock
from uuid import uuid4

from app.database import get_connection
from capture.git.reconcile import _branch_exists, _cwd_skip_reason, reconcile_runs, upsert_commits
from tests.conftest import SEED_REPOSITORY_ID, make_event


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
        "committed_at": datetime(2026, 6, 25, 10, 0, tzinfo=UTC),
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


# ── Helper unit tests ─────────────────────────────────────────────────────────

def test_cwd_skip_reason_missing_path():
    assert _cwd_skip_reason("/nonexistent/path/pytest-missing") == "stale_cwd"


def test_cwd_skip_reason_non_git_dir(tmp_path):
    assert _cwd_skip_reason(str(tmp_path)) == "non_git_cwd"


def test_cwd_skip_reason_git_repo(tmp_path):
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    assert _cwd_skip_reason(str(tmp_path)) is None


def test_branch_exists_true(tmp_path):
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t.com"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "T"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "checkout", "-b", "mytest"], check=True, capture_output=True)
    (tmp_path / "f.txt").write_text("x")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-m", "init"], check=True, capture_output=True)
    assert _branch_exists(str(tmp_path), "mytest") is True


def test_branch_exists_false(tmp_path):
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    assert _branch_exists(str(tmp_path), "no-such-branch") is False


# ── reconcile_runs skip-counter tests (mocked DB conn) ───────────────────────

def _fake_row(cwd: str, branch: str = "main") -> dict:
    now = datetime(2026, 6, 25, 10, 0, tzinfo=UTC)
    return {
        "run_id": uuid4(),
        "repository_id": SEED_REPOSITORY_ID,
        "branch": branch,
        "cwd": cwd,
        "started_at": now - timedelta(hours=1),
        "ended_at": now,
    }


def _mock_conn(rows: list[dict]) -> MagicMock:
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = rows
    return conn


def test_reconcile_skipped_stale_cwd():
    conn = _mock_conn([_fake_row("/nonexistent/stale/pytest-path")])
    result = reconcile_runs(conn)
    assert result["skipped_stale_cwd"] == 1
    assert result["commits_linked"] == 0
    assert result["runs_processed"] == 0


def test_reconcile_skipped_non_git_cwd(tmp_path):
    conn = _mock_conn([_fake_row(str(tmp_path))])
    result = reconcile_runs(conn)
    assert result["skipped_non_git_cwd"] == 1
    assert result["commits_linked"] == 0


def test_reconcile_skipped_missing_branch(tmp_path):
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    conn = _mock_conn([_fake_row(str(tmp_path), branch="deleted-branch")])
    result = reconcile_runs(conn)
    assert result["skipped_missing_branch"] == 1
    assert result["commits_linked"] == 0


def test_reconcile_summary_keys():
    """Result dict always contains all expected keys."""
    conn = _mock_conn([])
    result = reconcile_runs(conn)
    for key in ("runs_processed", "commits_linked", "skipped_stale_cwd",
                "skipped_non_git_cwd", "skipped_missing_branch",
                "failed_unexpected", "dry_run"):
        assert key in result, f"missing key: {key}"


def test_reconcile_dry_run_flag():
    conn = _mock_conn([])
    assert reconcile_runs(conn, dry_run=True)["dry_run"] is True
    assert reconcile_runs(conn, dry_run=False)["dry_run"] is False


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
