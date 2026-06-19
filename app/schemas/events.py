"""Normalized event schema and ingestion response models.

A single normalized event shape is shared by every capture adapter
(Claude Code, Codex, future tools). The fields split into run-level identity
(tool, session, project, repository, model, branch, cwd, intent) and
event-level detail (event_type, files_touched, occurred_at, raw_payload).
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class NormalizedEvent(BaseModel):
    """One normalized run event submitted to ``POST /runs/events``."""

    tool: str = Field(..., min_length=1, description="Tool slug, e.g. 'claude-code' or 'codex'.")
    session_id: str = Field(..., min_length=1, description="Tool-native session identifier.")
    event_type: str = Field(..., min_length=1, description="Normalized event type.")
    repository_id: str = Field(..., min_length=1)
    project_id: str = Field(..., min_length=1)

    model: str | None = None
    branch: str | None = None
    cwd: str | None = None
    intent: str | None = None

    files_touched: list[str] = Field(default_factory=list)
    occurred_at: datetime | None = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)

    # Optional but enables idempotent ingestion. When present and already seen,
    # the event is treated as a duplicate and not re-inserted.
    source_event_id: str | None = None


class EventIngestResponse(BaseModel):
    run_id: str
    event_id: str
    status: str = Field(description="'created' for a new event, 'duplicate' if already ingested.")
    redaction_status: str = Field(description="'redacted' or 'clean'.")
