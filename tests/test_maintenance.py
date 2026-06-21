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
        changed = ingestion.backfill_run_time_bounds(conn)
        assert changed >= 1
        row = conn.execute(
            "SELECT started_at, ended_at FROM runs WHERE run_id = %s", (run_id,)
        ).fetchone()
        assert row["started_at"] == t_early
        assert row["ended_at"] == t_late
        conn.rollback()


def test_backfill_is_idempotent():
    session_id = f"pytest-session-{uuid4().hex}"
    t = datetime(2026, 5, 3, 9, 0, tzinfo=UTC)
    with get_connection() as conn:
        run_id = _seed_run_with_events(conn, session_id, [t])
        conn.execute(
            "UPDATE runs SET started_at = NOW(), ended_at = NULL WHERE run_id = %s",
            (run_id,),
        )
        first = ingestion.backfill_run_time_bounds(conn)
        assert first >= 1
        _ = ingestion.backfill_run_time_bounds(conn)
        # Second pass: assert our target row is still correct (stable).
        row = conn.execute(
            "SELECT started_at, ended_at FROM runs WHERE run_id = %s", (run_id,)
        ).fetchone()
        assert row["started_at"] == t and row["ended_at"] == t
        conn.rollback()


def _seed_run(conn, session_id):
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
    return run_id


def _seed_event(conn, run_id, session_id, event_type, usage=None):
    payload = {"message": {"usage": usage}} if usage is not None else {}
    ingestion.insert_run_event(
        conn,
        source_event_id=f"pytest-event-{uuid4().hex}",
        run_id=run_id,
        tool_id=T,
        session_id=session_id,
        event_type=event_type,
        files_touched=[],
        raw_payload=payload,
        redaction_status="clean",
        occurred_at=None,
    )


def test_backfill_usage_aggregates_tokens_and_counts():
    sid = f"pytest-session-{uuid4().hex}"
    with get_connection() as conn:
        run_id = _seed_run(conn, sid)
        _seed_event(
            conn,
            run_id,
            sid,
            "assistant_message",
            {"input_tokens": 100, "output_tokens": 40, "cache_read_input_tokens": 5},
        )
        _seed_event(
            conn,
            run_id,
            sid,
            "assistant_message",
            {"input_tokens": 10, "output_tokens": 4, "cache_read_input_tokens": 1},
        )
        _seed_event(conn, run_id, sid, "tool_use")
        _seed_event(conn, run_id, sid, "command_run")
        ingestion.backfill_usage_metrics(conn)
        row = conn.execute(
            "SELECT input_tokens, output_tokens, cached_input_tokens, iteration_count, "
            "tool_calls_count, cost_usd, cost_source FROM usage_metrics WHERE run_id = %s",
            (run_id,),
        ).fetchone()
        conn.rollback()
    assert row["input_tokens"] == 110
    assert row["output_tokens"] == 44
    assert row["cached_input_tokens"] == 6
    assert row["iteration_count"] == 2
    assert row["tool_calls_count"] == 2
    assert row["cost_usd"] is None
    assert row["cost_source"] == "unavailable"


def test_backfill_usage_null_not_zero_for_no_usage_run():
    sid = f"pytest-session-{uuid4().hex}"
    with get_connection() as conn:
        run_id = _seed_run(conn, sid)
        _seed_event(conn, run_id, sid, "user_prompt")  # no usage, not a tool call
        ingestion.backfill_usage_metrics(conn)
        row = conn.execute(
            "SELECT input_tokens, output_tokens, iteration_count, tool_calls_count "
            "FROM usage_metrics WHERE run_id = %s",
            (run_id,),
        ).fetchone()
        conn.rollback()
    assert row["input_tokens"] is None  # NULL, not 0
    assert row["output_tokens"] is None
    assert row["iteration_count"] == 0  # a count, legitimately 0
    assert row["tool_calls_count"] == 0


def test_backfill_usage_idempotent_in_result():
    sid = f"pytest-session-{uuid4().hex}"
    with get_connection() as conn:
        run_id = _seed_run(conn, sid)
        _seed_event(
            conn,
            run_id,
            sid,
            "assistant_message",
            {"input_tokens": 7, "output_tokens": 3, "cache_read_input_tokens": 0},
        )
        ingestion.backfill_usage_metrics(conn)
        ingestion.backfill_usage_metrics(conn)  # second pass
        row = conn.execute(
            "SELECT input_tokens, iteration_count FROM usage_metrics WHERE run_id = %s",
            (run_id,),
        ).fetchone()
        conn.rollback()
    assert row["input_tokens"] == 7  # stable, not doubled
    assert row["iteration_count"] == 1


def test_main_backfill_run_times_subcommand(capsys):
    rc = maintenance.main(["backfill-run-times"])
    assert rc == 0
    assert "runs updated:" in capsys.readouterr().out


def test_main_backfill_usage_subcommand(capsys):
    rc = maintenance.main(["backfill-usage"])
    assert rc == 0
    assert "usage rows written:" in capsys.readouterr().out
