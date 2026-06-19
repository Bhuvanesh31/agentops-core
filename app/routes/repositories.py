"""Read-only repository listing, used by capture adapters to resolve
a session's git remote to a registered (project_id, repository_id)."""

import psycopg
from fastapi import APIRouter, Depends

from app.database import db_dependency
from app.models import ingestion
from app.schemas.repositories import RepositoryOut

router = APIRouter(tags=["repositories"])


@router.get("/repositories", response_model=list[RepositoryOut])
def list_repositories(conn: psycopg.Connection = Depends(db_dependency)) -> list[dict]:
    return ingestion.list_repositories(conn)
