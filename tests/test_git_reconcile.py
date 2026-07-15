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
