# tests/test_maintenance.py
from datetime import UTC, datetime
from uuid import uuid4

from app import maintenance
from app.database import get_connection
from app.models import ingestion

P, R, T = "agentops-core", "agentops-core-main", "claude-code"


def _seed_run_with_events(conn, session_id, times):
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
    for ts in times:
        ingestion.insert_run_event(
            conn,
            source_event_id=f"pytest-event-{uuid4().hex}",
            run_id=run_id,
            tool_id=T,
            session_id=session_id,
            event_type="file_edited",
            files_touched=[],
            raw_payload={},
            redaction_status="clean",
            occurred_at=ts,
        )
    return run_id


def test_backfill_sets_min_and_max_from_events():
    session_id = f"pytest-session-{uuid4().hex}"
    t_early = datetime(2026, 5, 2, 8, 0, tzinfo=UTC)
    t_late = datetime(2026, 5, 2, 11, 0, tzinfo=UTC)
    with get_connection() as conn:
        run_id = _seed_run_with_events(conn, session_id, [t_late, t_early])
        # Force a wrong started_at and NULL ended_at to simulate pre-fix data.
        conn.execute(
            "UPDATE runs SET started_at = NOW(), ended_at = NULL WHERE run_id = %s",
            (run_id,),
        )
    with get_connection() as conn:
        changed = ingestion.backfill_run_time_bounds(conn)
    assert changed >= 1
    with get_connection() as conn:
        row = conn.execute(
            "SELECT started_at, ended_at FROM runs WHERE run_id = %s", (run_id,)
        ).fetchone()
    assert row["started_at"] == t_early
    assert row["ended_at"] == t_late


def test_backfill_is_idempotent():
    session_id = f"pytest-session-{uuid4().hex}"
    t = datetime(2026, 5, 3, 9, 0, tzinfo=UTC)
    with get_connection() as conn:
        run_id = _seed_run_with_events(conn, session_id, [t])
        conn.execute(
            "UPDATE runs SET started_at = NOW(), ended_at = NULL WHERE run_id = %s",
            (run_id,),
        )
    with get_connection() as conn:
        first = ingestion.backfill_run_time_bounds(conn)
    with get_connection() as conn:
        _ = ingestion.backfill_run_time_bounds(conn)
    assert first >= 1
    # Second pass changes nothing for the already-correct row.
    with get_connection() as conn:
        row = conn.execute(
            "SELECT started_at, ended_at FROM runs WHERE run_id = %s", (run_id,)
        ).fetchone()
    assert row["started_at"] == t and row["ended_at"] == t
    # second may be 0 globally only if no other run needed fixing; assert our row stable instead.


def test_main_runs_and_returns_zero(capsys):
    rc = maintenance.main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "runs updated:" in out
