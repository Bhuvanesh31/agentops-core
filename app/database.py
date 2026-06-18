"""PostgreSQL connection management.

A single process-wide :class:`psycopg_pool.ConnectionPool` is opened on startup
and closed on shutdown. Routes acquire a connection via the ``get_connection``
context manager or the ``db_dependency`` FastAPI dependency.

``pool.connection()`` commits on a clean exit and rolls back on exception, so a
request either persists fully or not at all.
"""

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import get_settings

_pool: ConnectionPool | None = None


def init_pool() -> ConnectionPool:
    """Create the connection pool if it does not yet exist."""
    global _pool
    if _pool is None:
        settings = get_settings()
        _pool = ConnectionPool(
            conninfo=settings.database_url,
            min_size=1,
            max_size=10,
            kwargs={"row_factory": dict_row},
            open=True,
        )
    return _pool


def close_pool() -> None:
    """Close the connection pool on shutdown."""
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def get_connection() -> Iterator[psycopg.Connection]:
    """Yield a pooled connection; commit on success, roll back on error."""
    pool = init_pool()
    with pool.connection() as conn:
        yield conn


def db_dependency() -> Iterator[psycopg.Connection]:
    """FastAPI dependency that yields a pooled connection per request."""
    with get_connection() as conn:
        yield conn
