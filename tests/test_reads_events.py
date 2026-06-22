from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.database import get_connection
from app.models import ingestion

P, R, T = "agentops-core", "agentops-core-main", "claude-code"


def _seed_run_with_events(session_id):
    """Create a run with two events inserted OUT of time order."""
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
        base = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        # Insert the LATER event first to prove ORDER BY occurred_at ASC.
        ingestion.insert_run_event(
            conn,
            source_event_id=f"pytest-{uuid4().hex}",
            run_id=run_id,
            tool_id=T,
            session_id=session_id,
            event_type="assistant_message",
            files_touched=[],
            raw_payload={"seq": 2},
            redaction_status="clean",
            occurred_at=base + timedelta(minutes=5),
        )
        ingestion.insert_run_event(
            conn,
            source_event_id=f"pytest-{uuid4().hex}",
            run_id=run_id,
            tool_id=T,
            session_id=session_id,
            event_type="tool_use",
            files_touched=["a.py"],
            raw_payload={"seq": 1, "nested": {"k": "v"}},
            redaction_status="redacted",
            occurred_at=base,
        )
    return str(run_id)


def test_events_returned_oldest_first(client):
    sid = f"pytest-session-{uuid4().hex}"
    run_id = _seed_run_with_events(sid)
    resp = client.get(f"/runs/{run_id}/events")
    assert resp.status_code == 200
    body = resp.json()
    assert [e["raw_payload"]["seq"] for e in body] == [1, 2]


def test_events_unknown_run_is_empty_list(client):
    resp = client.get(f"/runs/{uuid4()}/events")
    assert resp.status_code == 200
    assert resp.json() == []


def test_events_full_raw_payload_roundtrips(client):
    sid = f"pytest-session-{uuid4().hex}"
    run_id = _seed_run_with_events(sid)
    body = client.get(f"/runs/{run_id}/events").json()
    first = body[0]
    assert first["raw_payload"] == {"seq": 1, "nested": {"k": "v"}}


def test_events_expose_redaction_and_files(client):
    sid = f"pytest-session-{uuid4().hex}"
    run_id = _seed_run_with_events(sid)
    body = client.get(f"/runs/{run_id}/events").json()
    first = body[0]
    assert first["redaction_status"] == "redacted"
    assert first["files_touched"] == ["a.py"]
    assert first["event_type"] == "tool_use"
