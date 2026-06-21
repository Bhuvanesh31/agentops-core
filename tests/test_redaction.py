"""Secret redaction tests: unit-level and end-to-end through ingestion."""

import json
from uuid import uuid4

from app.database import get_connection
from app.redaction import REDACTED, redact_payload
from tests.conftest import make_event


def test_redacts_value_by_sensitive_key():
    payload = {"api_key": "totally-not-a-real-key", "user": "alice"}
    redacted, status = redact_payload(payload)
    assert status == "redacted"
    assert redacted["api_key"] == REDACTED
    assert redacted["user"] == "alice"


def test_redacts_secret_shaped_value_in_free_text():
    payload = {"command": "export TOKEN=sk-ant-api03-abcdefghijklmnopqrstuvwx && run"}
    redacted, status = redact_payload(payload)
    assert status == "redacted"
    assert "sk-ant" not in json.dumps(redacted)
    assert REDACTED in redacted["command"]


def test_redacts_nested_structures():
    payload = {"outer": {"items": [{"password": "hunter2"}, {"ok": "value"}]}}
    redacted, status = redact_payload(payload)
    assert status == "redacted"
    assert redacted["outer"]["items"][0]["password"] == REDACTED
    assert redacted["outer"]["items"][1]["ok"] == "value"


def test_clean_payload_is_unchanged():
    payload = {"event": "file_edited", "lines": 12}
    redacted, status = redact_payload(payload)
    assert status == "clean"
    assert redacted == payload


def test_numeric_token_counts_are_not_redacted():
    # Regression: keys like input_tokens/output_tokens contain the substring
    # "token" but hold integer counts, not secrets. They must survive so token
    # usage stays available for cost analysis. String secrets under sensitive
    # keys must still be redacted.
    payload = {
        "usage": {
            "input_tokens": 1234,
            "output_tokens": 56,
            "cache_read_input_tokens": 0,
            "cache_creation": {"ephemeral_5m_input_tokens": 7},
        },
        "api_token": "sk-ant-secretvaluexxxxxxxxxxxx",
    }
    redacted, status = redact_payload(payload)
    assert status == "redacted"
    assert redacted["usage"]["input_tokens"] == 1234
    assert redacted["usage"]["output_tokens"] == 56
    assert redacted["usage"]["cache_read_input_tokens"] == 0
    assert redacted["usage"]["cache_creation"]["ephemeral_5m_input_tokens"] == 7
    assert redacted["api_token"] == REDACTED


def test_ingested_payload_is_redacted_in_database(client):
    source_event_id = f"pytest-event-{uuid4().hex}"
    secret = "sk-ant-api03-zzzyyyxxxwwwvvvuuutttsss"
    payload = make_event(
        source_event_id=source_event_id,
        raw_payload={"api_key": secret, "note": "ran the build"},
    )

    response = client.post("/runs/events", json=payload)
    assert response.status_code == 201
    assert response.json()["redaction_status"] == "redacted"

    # The secret must never reach storage.
    with get_connection() as conn:
        row = conn.execute(
            "SELECT raw_payload, redaction_status FROM run_events WHERE source_event_id = %s",
            (source_event_id,),
        ).fetchone()

    assert row is not None
    assert row["redaction_status"] == "redacted"
    stored = json.dumps(row["raw_payload"])
    assert secret not in stored
    assert REDACTED in stored
