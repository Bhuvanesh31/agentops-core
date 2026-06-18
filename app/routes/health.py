"""Health endpoint.

Verifies that the API process is running and that PostgreSQL is reachable via a
trivial ``SELECT 1``. Returns 200 when both hold, 503 otherwise. The driver
exception is deliberately not surfaced, to avoid leaking connection details.
"""

import logging

from fastapi import APIRouter, Response

from app.database import get_connection

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health")
def health(response: Response) -> dict[str, str]:
    database_reachable = False
    try:
        with get_connection() as conn:
            conn.execute("SELECT 1")
        database_reachable = True
    except Exception:  # noqa: BLE001 - report status, never leak details
        logger.exception("Health check could not reach PostgreSQL")

    if not database_reachable:
        response.status_code = 503

    return {
        "status": "ok" if database_reachable else "degraded",
        "api": "ok",
        "database": "reachable" if database_reachable else "unreachable",
    }
