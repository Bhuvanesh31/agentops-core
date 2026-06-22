from uuid import uuid4

from app.database import get_connection
from tests.conftest import make_event


def _usage_row(run_id):
    with get_connection() as conn:
        return conn.execute(
            "SELECT input_tokens, output_tokens, cached_input_tokens, iteration_count, "
            "tool_calls_count FROM usage_metrics WHERE run_id = %s",
            (run_id,),
        ).fetchone()


def _assistant_payload(inp, out, cache):
    return {
        "message": {
            "usage": {"input_tokens": inp, "output_tokens": out, "cache_read_input_tokens": cache}
        }
    }


def test_goforward_accumulates_usage(client):
    sid = f"pytest-session-{uuid4().hex}"
    r1 = client.post(
        "/runs/events",
        json=make_event(
            session_id=sid,
            event_type="assistant_message",
            raw_payload=_assistant_payload(100, 40, 5),
            source_event_id=f"pytest-event-{uuid4().hex}",
        ),
    )
    client.post(
        "/runs/events",
        json=make_event(
            session_id=sid,
            event_type="assistant_message",
            raw_payload=_assistant_payload(10, 4, 1),
            source_event_id=f"pytest-event-{uuid4().hex}",
        ),
    )
    client.post(
        "/runs/events",
        json=make_event(
            session_id=sid,
            event_type="tool_use",
            raw_payload={},
            source_event_id=f"pytest-event-{uuid4().hex}",
        ),
    )
    row = _usage_row(r1.json()["run_id"])
    assert row["input_tokens"] == 110
    assert row["output_tokens"] == 44
    assert row["cached_input_tokens"] == 6
    assert row["iteration_count"] == 2
    assert row["tool_calls_count"] == 1


def test_goforward_duplicate_not_double_counted(client):
    sid = f"pytest-session-{uuid4().hex}"
    seid = f"pytest-event-{uuid4().hex}"
    payload = make_event(
        session_id=sid,
        event_type="assistant_message",
        raw_payload=_assistant_payload(50, 20, 0),
        source_event_id=seid,
    )
    r1 = client.post("/runs/events", json=payload)
    client.post("/runs/events", json=payload)  # duplicate source_event_id
    row = _usage_row(r1.json()["run_id"])
    assert row["input_tokens"] == 50  # not 100
    assert row["iteration_count"] == 1


def test_goforward_non_usage_event_leaves_tokens_null(client):
    sid = f"pytest-session-{uuid4().hex}"
    r1 = client.post(
        "/runs/events",
        json=make_event(
            session_id=sid,
            event_type="user_prompt",
            raw_payload={},
            source_event_id=f"pytest-event-{uuid4().hex}",
        ),
    )
    row = _usage_row(r1.json()["run_id"])
    # No usage_metrics row at all (no usage/tool events) -> None
    assert row is None
