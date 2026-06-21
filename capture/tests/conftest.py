"""Fixtures for capture tests: an in-process API client and DB cleanup."""

from collections.abc import Iterator

import httpx
import pytest
from starlette.testclient import TestClient

from app.database import get_connection
from app.main import app


@pytest.fixture
def asgi_client() -> Iterator[httpx.Client]:
    # TestClient is an httpx.Client subclass that drives the ASGI app in-process.
    # httpx.ASGITransport is async-only and cannot be used with the sync Client.
    with TestClient(app, base_url="http://test") as http:
        yield http


@pytest.fixture(autouse=True)
def cleanup_pytest_rows() -> Iterator[None]:
    yield
    try:
        with get_connection() as conn:
            conn.execute("DELETE FROM runs WHERE session_id LIKE 'pytest-%'")
            conn.execute("DELETE FROM run_events WHERE source_event_id LIKE 'pytest-%'")
            conn.execute("DELETE FROM repositories WHERE repository_id LIKE 'pytest-%'")
    except Exception:
        pass
