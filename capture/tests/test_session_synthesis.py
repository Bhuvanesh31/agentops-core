from capture.claude_code.normalize import synthesize_session_events

FIRST = {
    "sessionId": "s1",
    "cwd": "/repo",
    "gitBranch": "main",
    "timestamp": "2026-06-19T00:00:00Z",
}
LAST = {"sessionId": "s1", "cwd": "/repo", "gitBranch": "main", "timestamp": "2026-06-19T01:00:00Z"}


def test_idle_session_gets_started_and_ended():
    events = synthesize_session_events(FIRST, LAST, file_mtime=0.0, now=10_000.0, idle_seconds=3600)
    types = [e["event_type"] for e in events]
    assert types == ["session_started", "session_ended"]
    assert events[0]["source_event_id"] == "s1#session_started"
    assert events[1]["source_event_id"] == "s1#session_ended"
    assert events[0]["occurred_at"] == "2026-06-19T00:00:00Z"
    assert events[1]["occurred_at"] == "2026-06-19T01:00:00Z"


def test_fresh_session_gets_only_started():
    events = synthesize_session_events(
        FIRST, LAST, file_mtime=9_999.0, now=10_000.0, idle_seconds=3600
    )
    assert [e["event_type"] for e in events] == ["session_started"]
