"""FastAPI application entry point.

The database connection pool is opened on startup and closed on shutdown via the
lifespan handler.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.database import close_pool, init_pool
from app.routes import events, health, repositories


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_pool()
    try:
        yield
    finally:
        close_pool()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
    )
    app.include_router(health.router)
    app.include_router(events.router)
    app.include_router(repositories.router)
    return app


app = create_app()
