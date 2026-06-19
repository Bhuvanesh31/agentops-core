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
