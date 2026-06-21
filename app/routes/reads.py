import psycopg
from fastapi import APIRouter, Depends, Query

from app.database import db_dependency
from app.models import reads
from app.schemas.reads import RunListItem

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
