"""Read-side queries over run_overview and per-project rollups (raw SQL)."""

from typing import Any

import psycopg

_FILTER_COLUMNS = ("project_id", "repository_id", "status", "tool_id")


def list_runs(
    conn: psycopg.Connection,
    *,
    project_id: str | None = None,
    repository_id: str | None = None,
    status: str | None = None,
    tool_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List runs from run_overview with optional equality filters, newest first."""
    values = {
        "project_id": project_id,
        "repository_id": repository_id,
        "status": status,
        "tool_id": tool_id,
    }
    params: dict[str, Any] = {}
    clauses = []
    for col in _FILTER_COLUMNS:  # column names are a fixed whitelist, not user input
        if values[col] is not None:
            clauses.append(f"{col} = %({col})s")
            params[col] = values[col]
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    params["limit"] = limit
    params["offset"] = offset
    return conn.execute(
        f"SELECT * FROM run_overview{where} "
        "ORDER BY started_at DESC NULLS LAST LIMIT %(limit)s OFFSET %(offset)s",
        params,
    ).fetchall()


def get_run(conn: psycopg.Connection, run_id: str) -> dict[str, Any] | None:
    """Return one run_overview row by run_id, or None if not found."""
    return conn.execute("SELECT * FROM run_overview WHERE run_id = %s", (run_id,)).fetchone()


def list_run_events(conn: psycopg.Connection, run_id: str) -> list[dict[str, Any]]:
    """Return one run's events oldest-first; empty list if the run is unknown."""
    return conn.execute(
        """
        SELECT event_id, event_type, tool_name, files_touched,
               redaction_status, occurred_at, received_at, raw_payload
        FROM run_events
        WHERE run_id = %s
        ORDER BY occurred_at ASC NULLS LAST, received_at ASC
        """,
        (run_id,),
    ).fetchall()


def list_run_commits(conn: psycopg.Connection, run_id: str) -> list[dict[str, Any]]:
    """Return commits linked to a run, oldest-first. Empty list if none."""
    return conn.execute(
        """
        SELECT commit_sha, branch, author_name, author_email,
               commit_message, committed_at
        FROM commits
        WHERE run_id = %s
        ORDER BY committed_at ASC NULLS LAST
        """,
        (run_id,),
    ).fetchall()


def project_overview(conn: psycopg.Connection) -> list[dict[str, Any]]:
    """Per-project rollup: run count, token totals, and activity time range."""
    return conn.execute(
        """
        SELECT
            p.project_id,
            p.project_name,
            COUNT(r.run_id) AS run_count,
            SUM(u.input_tokens) AS input_tokens,
            SUM(u.output_tokens) AS output_tokens,
            SUM(u.cached_input_tokens) AS cached_input_tokens,
            MIN(r.started_at) AS earliest,
            MAX(COALESCE(r.ended_at, r.started_at)) AS latest_activity
        FROM projects p
        JOIN runs r ON r.project_id = p.project_id
        LEFT JOIN usage_metrics u ON u.run_id = r.run_id
        GROUP BY p.project_id, p.project_name
        ORDER BY run_count DESC
        """
    ).fetchall()
