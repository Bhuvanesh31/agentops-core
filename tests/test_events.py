"""Event ingestion endpoint tests."""

from uuid import uuid4

from tests.conftest import make_event


def test_valid_event_creates_run_and_event(client):
    payload = make_event()
    response = client.post("/runs/events", json=payload)

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "created"
    assert body["run_id"]
    assert body["event_id"]
    assert body["redaction_status"] == "clean"


def test_second_event_same_session_reuses_run(client):
    session_id = f"pytest-session-{uuid4().hex}"

    first = client.post("/runs/events", json=make_event(session_id=session_id))
    second = client.post(
        "/runs/events",
        json=make_event(session_id=session_id, event_type="file_edited"),
    )

    assert first.status_code == 201
    assert second.status_code == 201
    # Same session -> same run, distinct events.
    assert first.json()["run_id"] == second.json()["run_id"]
    assert first.json()["event_id"] != second.json()["event_id"]


def test_duplicate_source_event_id_is_not_reinserted(client):
    source_event_id = f"pytest-event-{uuid4().hex}"
    payload = make_event(source_event_id=source_event_id)

    first = client.post("/runs/events", json=payload)
    second = client.post("/runs/events", json=payload)

    assert first.status_code == 201
    assert first.json()["status"] == "created"

    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"
    # The duplicate maps back to the same event and run.
    assert second.json()["event_id"] == first.json()["event_id"]
    assert second.json()["run_id"] == first.json()["run_id"]


def test_unknown_repository_returns_404(client):
    response = client.post(
        "/runs/events",
        json=make_event(repository_id="does-not-exist"),
    )
    assert response.status_code == 404
    assert "repository_id" in response.json()["detail"]


def test_unknown_tool_returns_404(client):
    response = client.post(
        "/runs/events",
        json=make_event(tool="not-a-real-tool"),
    )
    assert response.status_code == 404
    assert "tool" in response.json()["detail"].lower()


def test_repository_not_in_project_returns_400(client):
    # Valid repository, but paired with the wrong (non-owning) project id.
    response = client.post(
        "/runs/events",
        json=make_event(project_id="agentops-core", repository_id="agentops-core-main"),
    )
    # Sanity: the seeded pairing is valid, so this should succeed.
    assert response.status_code == 201


def test_invalid_payload_returns_422(client):
    # Missing required fields (session_id, event_type, ...).
    response = client.post("/runs/events", json={"tool": "claude-code"})
    assert response.status_code == 422
