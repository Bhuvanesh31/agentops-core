"""Persistence operations for run-event ingestion.

All functions take an open psycopg connection so the caller controls the
transaction boundary (the request-scoped connection commits on success).
"""

from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Json


def get_project(conn: psycopg.Connection, project_id: str) -> dict[str, Any] | None:
    return conn.execute(
        "SELECT project_id FROM projects WHERE project_id = %s",
        (project_id,),
    ).fetchone()


def get_repository(conn: psycopg.Connection, repository_id: str) -> dict[str, Any] | None:
    return conn.execute(
        "SELECT repository_id, project_id FROM repositories WHERE repository_id = %s",
        (repository_id,),
    ).fetchone()


def get_tool(conn: psycopg.Connection, tool_id: str) -> dict[str, Any] | None:
    return conn.execute(
        "SELECT tool_id FROM tools WHERE tool_id = %s",
        (tool_id,),
    ).fetchone()


def get_or_create_run(
    conn: psycopg.Connection,
    *,
    project_id: str,
    repository_id: str,
    tool_id: str,
    session_id: str,
    model: str | None,
    branch: str | None,
    cwd: str | None,
    intent: str | None,
) -> tuple[UUID, bool]:
    """Locate the run for this (tool, repository, session) or create it.

    Relies on the ``runs_tool_repository_session_unique`` constraint. Returns
    ``(run_id, created)`` where ``created`` is True when a new run was inserted.
    """
    existing = conn.execute(
        """
        SELECT run_id FROM runs
        WHERE tool_id = %s AND repository_id = %s AND session_id = %s
        """,
        (tool_id, repository_id, session_id),
    ).fetchone()
    if existing:
        return existing["run_id"], False

    inserted = conn.execute(
        """
        INSERT INTO runs (
            project_id, repository_id, tool_id, session_id,
            model, branch, cwd, intent
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (tool_id, repository_id, session_id) DO NOTHING
        RETURNING run_id
        """,
        (project_id, repository_id, tool_id, session_id, model, branch, cwd, intent),
    ).fetchone()
    if inserted:
        return inserted["run_id"], True

    # A concurrent request created the run between our SELECT and INSERT.
    row = conn.execute(
        """
        SELECT run_id FROM runs
        WHERE tool_id = %s AND repository_id = %s AND session_id = %s
        """,
        (tool_id, repository_id, session_id),
    ).fetchone()
    return row["run_id"], False


def insert_run_event(
    conn: psycopg.Connection,
    *,
    source_event_id: str | None,
    run_id: UUID,
    tool_id: str,
    session_id: str,
    event_type: str,
    files_touched: list[str],
    raw_payload: dict[str, Any],
    redaction_status: str,
    occurred_at: datetime | None,
) -> tuple[UUID, bool]:
    """Insert an event, skipping duplicates by ``source_event_id``.

    Returns ``(event_id, is_duplicate)``. When ``source_event_id`` is provided
    and already exists, the existing event id is returned with
    ``is_duplicate=True`` and nothing new is written.
    """
    if source_event_id is not None:
        existing = conn.execute(
            "SELECT event_id FROM run_events WHERE source_event_id = %s",
            (source_event_id,),
        ).fetchone()
        if existing:
            return existing["event_id"], True

    row = conn.execute(
        """
        INSERT INTO run_events (
            source_event_id, run_id, tool_id, session_id,
            event_type, tool_name, files_touched, raw_payload,
            redaction_status, ingestion_status, occurred_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'processed', %s)
        ON CONFLICT (source_event_id) DO NOTHING
        RETURNING event_id
        """,
        (
            source_event_id,
            run_id,
            tool_id,
            session_id,
            event_type,
            tool_id,
            files_touched,
            Json(raw_payload),
            redaction_status,
            occurred_at,
        ),
    ).fetchone()
    if row:
        return row["event_id"], False

    # Lost a race on the unique source_event_id; fetch the winner.
    existing = conn.execute(
        "SELECT event_id FROM run_events WHERE source_event_id = %s",
        (source_event_id,),
    ).fetchone()
    return existing["event_id"], True


def update_run_time_bounds(
    conn: psycopg.Connection, *, run_id: UUID, occurred_at: datetime
) -> None:
    """Fold one event's occurred_at into the run's started_at/ended_at.

    started_at takes the earliest time seen (LEAST against the NOW() default on a
    fresh run); ended_at takes the latest (COALESCE seeds it from NULL on the
    first event, GREATEST extends it thereafter).
    """
    conn.execute(
        """
        UPDATE runs
        SET started_at = LEAST(started_at, %(occ)s),
            ended_at   = GREATEST(COALESCE(ended_at, %(occ)s), %(occ)s),
            updated_at = NOW()
        WHERE run_id = %(run_id)s
        """,
        {"occ": occurred_at, "run_id": run_id},
    )


def backfill_run_time_bounds(conn: psycopg.Connection) -> int:
    """Recompute started_at/ended_at for all runs from their events.

    Uses MIN/MAX of run_events.occurred_at (ignoring NULLs). The IS DISTINCT FROM
    guard updates only rows whose bounds actually change, so re-runs are no-ops.
    Returns the number of rows updated.
    """
    result = conn.execute(
        """
        UPDATE runs r
        SET started_at = sub.min_occ,
            ended_at   = sub.max_occ,
            updated_at = NOW()
        FROM (
            SELECT run_id,
                   MIN(occurred_at) AS min_occ,
                   MAX(occurred_at) AS max_occ
            FROM run_events
            WHERE occurred_at IS NOT NULL
            GROUP BY run_id
        ) sub
        WHERE r.run_id = sub.run_id
          AND (r.started_at IS DISTINCT FROM sub.min_occ
               OR r.ended_at IS DISTINCT FROM sub.max_occ)
        """
    )
    return result.rowcount


def backfill_usage_metrics(conn: psycopg.Connection) -> int:
    """Aggregate per-run token usage from run_events into usage_metrics.

    Sums token counts from assistant_message events' raw_payload.message.usage,
    counts iterations (assistant_message) and tool calls (tool_use/command_run/
    file_edit). cost_usd/cost_source are left untouched on conflict so a future
    'reported' cost is never clobbered. Idempotent in result. Returns rows written.
    """
    result = conn.execute(
        """
        INSERT INTO usage_metrics (
            run_id, input_tokens, output_tokens, cached_input_tokens,
            iteration_count, tool_calls_count, cost_source
        )
        SELECT
            run_id,
            SUM((raw_payload->'message'->'usage'->>'input_tokens')::bigint)
                FILTER (WHERE event_type = 'assistant_message'),
            SUM((raw_payload->'message'->'usage'->>'output_tokens')::bigint)
                FILTER (WHERE event_type = 'assistant_message'),
            SUM((raw_payload->'message'->'usage'->>'cache_read_input_tokens')::bigint)
                FILTER (WHERE event_type = 'assistant_message'),
            COUNT(*) FILTER (WHERE event_type = 'assistant_message'),
            COUNT(*) FILTER (WHERE event_type IN ('tool_use', 'command_run', 'file_edit')),
            'unavailable'
        FROM run_events
        GROUP BY run_id
        ON CONFLICT (run_id) DO UPDATE SET
            input_tokens        = EXCLUDED.input_tokens,
            output_tokens       = EXCLUDED.output_tokens,
            cached_input_tokens = EXCLUDED.cached_input_tokens,
            iteration_count     = EXCLUDED.iteration_count,
            tool_calls_count    = EXCLUDED.tool_calls_count,
            updated_at          = NOW()
        """
    )
    return result.rowcount


def list_repositories(conn: psycopg.Connection) -> list[dict[str, Any]]:
    """Return active repositories for identity resolution."""
    return conn.execute(
        """
        SELECT repository_id, project_id, remote_url
        FROM repositories
        WHERE is_active = TRUE
        ORDER BY repository_id
        """
    ).fetchall()


def upsert_repository(
    conn: psycopg.Connection,
    *,
    repository_id: str,
    project_id: str,
    repository_name: str,
    remote_url: str | None,
    local_path: str | None,
    default_branch: str,
    is_active: bool,
) -> dict[str, Any]:
    """Insert or update a repository; return {repository_id, project_id, remote_url}."""
    return conn.execute(
        """
        INSERT INTO repositories (
            repository_id, project_id, repository_name,
            remote_url, local_path, default_branch, is_active
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (repository_id) DO UPDATE SET
            project_id      = EXCLUDED.project_id,
            repository_name = EXCLUDED.repository_name,
            remote_url      = EXCLUDED.remote_url,
            local_path      = EXCLUDED.local_path,
            default_branch  = EXCLUDED.default_branch,
            is_active       = EXCLUDED.is_active,
            updated_at      = NOW()
        RETURNING repository_id, project_id, remote_url
        """,
        (
            repository_id,
            project_id,
            repository_name,
            remote_url,
            local_path,
            default_branch,
            is_active,
        ),
    ).fetchone()
