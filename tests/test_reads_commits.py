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
