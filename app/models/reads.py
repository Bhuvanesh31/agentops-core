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
