from uuid import uuid4

from app.database import get_connection
from app.models import ingestion

P, R, T = "agentops-core", "agentops-core-main", "claude-code"


def _seed_run(session_id, status="active"):
    with get_connection() as conn:
        run_id, _ = ingestion.get_or_create_run(
            conn,
            project_id=P,
            repository_id=R,
            tool_id=T,
            session_id=session_id,
            model="claude-opus-4-8",
            branch="main",
            cwd="/tmp/work",
            intent=None,
        )
        if status != "active":
            conn.execute("UPDATE runs SET status = %s WHERE run_id = %s", (status, run_id))
    return str(run_id)


def test_list_runs_returns_seeded_run(client):
    sid = f"pytest-session-{uuid4().hex}"
    run_id = _seed_run(sid)
    resp = client.get("/runs", params={"project_id": P, "limit": 200})
    assert resp.status_code == 200
    ids = {r["run_id"] for r in resp.json()}
    assert run_id in ids


def test_list_runs_filter_by_status(client):
    sid = f"pytest-session-{uuid4().hex}"
    run_id = _seed_run(sid, status="completed")
    resp = client.get("/runs", params={"status": "completed", "limit": 200})
    assert resp.status_code == 200
    rows = resp.json()
    assert run_id in {r["run_id"] for r in rows}
    assert all(r["status"] == "completed" for r in rows)


def test_list_runs_pagination(client):
    resp = client.get("/runs", params={"limit": 2, "offset": 0})
    assert resp.status_code == 200
    assert len(resp.json()) <= 2


def test_list_runs_rejects_bad_limit(client):
    assert client.get("/runs", params={"limit": 9999}).status_code == 422
    assert client.get("/runs", params={"offset": -1}).status_code == 422
