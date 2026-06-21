from uuid import uuid4

from app.database import get_connection
from app.models import ingestion

P, R, T = "agentops-core", "agentops-core-main", "claude-code"


def _seed_run_with_usage(session_id, inp, out):
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
        conn.execute(
            "INSERT INTO usage_metrics (run_id, input_tokens, output_tokens, cost_source) "
            "VALUES (%s, %s, %s, 'unavailable')",
            (run_id, inp, out),
        )
    return str(run_id)


def test_get_run_returns_row_with_tokens(client):
    sid = f"pytest-session-{uuid4().hex}"
    run_id = _seed_run_with_usage(sid, 123, 45)
    resp = client.get(f"/runs/{run_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == run_id
    assert body["input_tokens"] == 123
    assert body["output_tokens"] == 45


def test_get_run_unknown_is_404(client):
    assert client.get(f"/runs/{uuid4()}").status_code == 404


def test_overview_sums_tokens_for_project(client):
    sid = f"pytest-session-{uuid4().hex}"
    _seed_run_with_usage(sid, 1000, 200)
    resp = client.get("/overview")
    assert resp.status_code == 200
    entry = next(e for e in resp.json() if e["project_id"] == P)
    assert entry["run_count"] >= 1
    assert entry["input_tokens"] is not None and entry["input_tokens"] >= 1000
