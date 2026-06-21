# tests/test_run_time_bounds.py
from datetime import UTC, datetime
from uuid import uuid4

from app.database import get_connection
from tests.conftest import make_event


def _run_bounds(run_id: str) -> tuple[datetime | None, datetime | None]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT started_at, ended_at FROM runs WHERE run_id = %s", (run_id,)
        ).fetchone()
    return row["started_at"], row["ended_at"]


def test_started_and_ended_track_event_times(client):
    session_id = f"pytest-session-{uuid4().hex}"
    early = "2026-05-01T10:00:00+00:00"
    late = "2026-05-01T12:30:00+00:00"
    # Post the LATER event first to prove ordering does not matter.
    r1 = client.post(
        "/runs/events",
        json=make_event(
            session_id=session_id, occurred_at=late, source_event_id=f"pytest-event-{uuid4().hex}"
        ),
    )
    client.post(
        "/runs/events",
        json=make_event(
            session_id=session_id,
            event_type="file_edited",
            occurred_at=early,
            source_event_id=f"pytest-event-{uuid4().hex}",
        ),
    )
    assert r1.status_code == 201
    run_id = r1.json()["run_id"]
    started, ended = _run_bounds(run_id)
    assert started == datetime(2026, 5, 1, 10, 0, tzinfo=UTC)
    assert ended == datetime(2026, 5, 1, 12, 30, tzinfo=UTC)


def test_fresh_run_started_at_snaps_back_from_now(client):
    # A new run's started_at defaults to NOW(); a past occurred_at must lower it.
    session_id = f"pytest-session-{uuid4().hex}"
    past = datetime(2026, 5, 1, 9, 0, tzinfo=UTC)
    client.post(
        "/runs/events",
        json=make_event(
            session_id=session_id,
            occurred_at=past.isoformat(),
            source_event_id=f"pytest-event-{uuid4().hex}",
        ),
    )
    with get_connection() as conn:
        row = conn.execute(
            "SELECT started_at FROM runs WHERE session_id = %s", (session_id,)
        ).fetchone()
    assert row["started_at"] == past  # not ~NOW()


def test_event_without_occurred_at_does_not_break(client):
    session_id = f"pytest-session-{uuid4().hex}"
    # occurred_at omitted -> None; must not raise and run still created.
    resp = client.post(
        "/runs/events",
        json=make_event(session_id=session_id, source_event_id=f"pytest-event-{uuid4().hex}"),
    )
    assert resp.status_code == 201
    started, ended = _run_bounds(resp.json()["run_id"])
    assert started is not None  # NOW() default retained
    assert ended is None  # no event time to set it
