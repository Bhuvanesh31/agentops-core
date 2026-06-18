"""Shared test fixtures.

Tests run against the live Postgres container (the same one Docker Compose
starts). They use seeded reference data and clean up after themselves by
deleting any rows whose synthetic ids start with ``pytest-``.
"""

from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.database import get_connection
from app.main import app

# Reference ids guaranteed to exist by database/seed.sql.
SEED_PROJECT_ID = "agentops-core"
SEED_REPOSITORY_ID = "agentops-core-main"
SEED_TOOL_ID = "claude-code"


@pytest.fixture(scope="session")
def client() -> Iterator[TestClient]:
    # The context manager triggers lifespan startup/shutdown (pool open/close).
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def unique_session_id() -> str:
    return f"pytest-session-{uuid4().hex}"


@pytest.fixture
def unique_event_id() -> str:
    return f"pytest-event-{uuid4().hex}"


def make_event(**overrides) -> dict:
    """Build a valid normalized event payload, with optional overrides."""
    payload = {
        "tool": SEED_TOOL_ID,
        "session_id": f"pytest-session-{uuid4().hex}",
        "event_type": "session_started",
        "repository_id": SEED_REPOSITORY_ID,
        "project_id": SEED_PROJECT_ID,
        "model": "claude-opus-4-8",
        "branch": "main",
        "cwd": "/tmp/work",
        "intent": "test ingestion",
        "files_touched": [],
        "raw_payload": {},
    }
    payload.update(overrides)
    return payload


@pytest.fixture(autouse=True)
def cleanup_pytest_rows() -> Iterator[None]:
    """Remove rows created by tests after each test."""
    yield
    try:
        with get_connection() as conn:
            # Deleting runs cascades to their run_events.
            conn.execute("DELETE FROM runs WHERE session_id LIKE 'pytest-%'")
            conn.execute("DELETE FROM run_events WHERE source_event_id LIKE 'pytest-%'")
    except Exception:
        # Cleanup is best-effort; never fail a test run on teardown.
        pass
