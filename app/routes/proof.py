import psycopg
from fastapi import APIRouter, Depends

from app.database import db_dependency
from capture.proof_export import build_proof_summary

router = APIRouter(tags=["proof"])


@router.get("/proof-summary")
def get_proof_summary(conn: psycopg.Connection = Depends(db_dependency)) -> dict:
    return build_proof_summary(conn)
