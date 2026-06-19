"""Repository response schema for identity resolution."""

from pydantic import BaseModel


class RepositoryOut(BaseModel):
    repository_id: str
    project_id: str
    remote_url: str | None = None
