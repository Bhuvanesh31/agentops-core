from capture.claude_code.normalize import synthesize_session_events

FIRST = {
    "sessionId": "s1",
    "cwd": "/repo",
    "gitBranch": "main",
    "timestamp": "2026-06-19T00:00:00Z",
}
LAST = {"sessionId": "s1", "cwd": "/repo", "gitBranch": "main", "timestamp": "2026-06-19T01:00:00Z"}


def test_idle_session_gets_started_and_ended():
    events = synthesize_session_events(
        "s1", FIRST, LAST, file_mtime=0.0, now=10_000.0, idle_seconds=3600
    )
    types = [e["event_type"] for e in events]
    assert types == ["session_started", "session_ended"]
    assert events[0]["source_event_id"] == "s1#session_started"
    assert events[1]["source_event_id"] == "s1#session_ended"
    assert events[0]["occurred_at"] == "2026-06-19T00:00:00Z"
    assert events[1]["occurred_at"] == "2026-06-19T01:00:00Z"


def test_fresh_session_gets_only_started():
    events = synthesize_session_events(
        "s1", FIRST, LAST, file_mtime=9_999.0, now=10_000.0, idle_seconds=3600
    )
    assert [e["event_type"] for e in events] == ["session_started"]


def test_session_id_used_even_when_boundary_lines_lack_it():
    # Regression: a trailing metadata line with no sessionId must not yield a
    # null session_id (which the API rejects with 422). Both synthesized events
    # use the canonical session id, and the ended event falls back to the first
    # line's cwd/branch.
    last_no_session = {"type": "summary", "timestamp": "2026-06-19T01:00:00Z"}
    events = synthesize_session_events(
        "scanonical", FIRST, last_no_session, file_mtime=0.0, now=10_000.0, idle_seconds=3600
    )
    assert events[0]["session_id"] == "scanonical"
    assert events[1]["session_id"] == "scanonical"
    assert events[1]["source_event_id"] == "scanonical#session_ended"
    assert events[1]["cwd"] == "/repo"
    assert events[1]["branch"] == "main"
    assert events[1]["occurred_at"] == "2026-06-19T01:00:00Z"
