"""Run-event ingestion endpoint.

Pipeline for ``POST /runs/events``:

1. Validate the payload (handled by the ``NormalizedEvent`` model).
2. Confirm the project, repository, and tool exist, and that the repository
   belongs to the project.
3. Create or locate the run for this (tool, repository, session).
4. Redact secrets from ``raw_payload`` before storage.
5. Insert the event, skipping duplicates by ``source_event_id``.
6. Return the run id and event status.
"""

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.database import db_dependency
from app.models import ingestion
from app.redaction import redact_payload
from app.schemas.events import EventIngestResponse, NormalizedEvent

router = APIRouter(prefix="/runs", tags=["events"])


@router.post("/events", response_model=EventIngestResponse)
def ingest_event(
    event: NormalizedEvent,
    response: Response,
    conn: psycopg.Connection = Depends(db_dependency),
) -> EventIngestResponse:
    if ingestion.get_project(conn, event.project_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown project_id: {event.project_id}",
        )

    repository = ingestion.get_repository(conn, event.repository_id)
    if repository is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown repository_id: {event.repository_id}",
        )
    if repository["project_id"] != event.project_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Repository {event.repository_id} does not belong to project {event.project_id}"
            ),
        )

    if ingestion.get_tool(conn, event.tool) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown tool: {event.tool}",
        )

    redacted_payload, redaction_status = redact_payload(event.raw_payload)

    run_id, _ = ingestion.get_or_create_run(
        conn,
        project_id=event.project_id,
        repository_id=event.repository_id,
        tool_id=event.tool,
        session_id=event.session_id,
        model=event.model,
        branch=event.branch,
        cwd=event.cwd,
        intent=event.intent,
    )

    event_id, is_duplicate = ingestion.insert_run_event(
        conn,
        source_event_id=event.source_event_id,
        run_id=run_id,
        tool_id=event.tool,
        session_id=event.session_id,
        event_type=event.event_type,
        files_touched=event.files_touched,
        raw_payload=redacted_payload,
        redaction_status=redaction_status,
        occurred_at=event.occurred_at,
    )

    if not is_duplicate and event.occurred_at is not None:
        ingestion.update_run_time_bounds(conn, run_id=run_id, occurred_at=event.occurred_at)

    if not is_duplicate:
        usage = None
        payload = event.raw_payload
        if isinstance(payload, dict):
            message = payload.get("message")
            if isinstance(message, dict) and isinstance(message.get("usage"), dict):
                usage = message["usage"]
        ingestion.record_event_usage(conn, run_id=run_id, event_type=event.event_type, usage=usage)

    response.status_code = status.HTTP_200_OK if is_duplicate else status.HTTP_201_CREATED

    return EventIngestResponse(
        run_id=str(run_id),
        event_id=str(event_id),
        status="duplicate" if is_duplicate else "created",
        redaction_status=redaction_status,
    )
