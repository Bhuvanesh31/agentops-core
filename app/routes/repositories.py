"""Repository endpoints — list and register repositories."""

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.database import db_dependency
from app.models import ingestion
from app.schemas.repositories import RepositoryCreate, RepositoryOut

router = APIRouter(tags=["repositories"])


@router.get("/repositories", response_model=list[RepositoryOut])
def list_repositories(conn: psycopg.Connection = Depends(db_dependency)) -> list[dict]:
    return ingestion.list_repositories(conn)


@router.post("/repositories", response_model=RepositoryOut, status_code=status.HTTP_201_CREATED)
def create_repository(
    repo: RepositoryCreate,
    response: Response,
    conn: psycopg.Connection = Depends(db_dependency),
) -> dict:
    if ingestion.get_project(conn, repo.project_id) is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown project_id: {repo.project_id}",
        )
    existed = ingestion.get_repository(conn, repo.repository_id) is not None
    row = ingestion.upsert_repository(conn, **repo.model_dump())
    response.status_code = status.HTTP_200_OK if existed else status.HTTP_201_CREATED
    return row
