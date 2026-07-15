import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query

from app.database import db_dependency
from app.models import reads
from app.schemas.reads import ProjectOverview, RunCommit, RunDetail, RunEvent, RunListItem

# NOTE: do not `from fastapi import status` here — the GET /runs `status` query
# param would shadow it. Use literal HTTP codes where needed (Task 4).
router = APIRouter(tags=["reads"])


@router.get("/runs", response_model=list[RunListItem])
def get_runs(
    project_id: str | None = None,
    repository_id: str | None = None,
    status: str | None = None,
    tool_id: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn: psycopg.Connection = Depends(db_dependency),
) -> list[dict]:
    return reads.list_runs(
        conn,
        project_id=project_id,
        repository_id=repository_id,
        status=status,
        tool_id=tool_id,
        limit=limit,
        offset=offset,
    )


@router.get("/runs/{run_id}", response_model=RunDetail)
def get_run(run_id: str, conn: psycopg.Connection = Depends(db_dependency)) -> dict:
    row = reads.get_run(conn, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Unknown run_id: {run_id}")
    return row


@router.get("/runs/{run_id}/events", response_model=list[RunEvent])
def get_run_events(
    run_id: str, conn: psycopg.Connection = Depends(db_dependency)
) -> list[dict]:
    return reads.list_run_events(conn, run_id)


@router.get("/runs/{run_id}/commits", response_model=list[RunCommit])
def get_run_commits(
    run_id: str, conn: psycopg.Connection = Depends(db_dependency)
) -> list[dict]:
    return reads.list_run_commits(conn, run_id)


@router.get("/overview", response_model=list[ProjectOverview])
def get_overview(conn: psycopg.Connection = Depends(db_dependency)) -> list[dict]:
    return reads.project_overview(conn)
