"""Repository request/response schemas."""

from pydantic import BaseModel


class RepositoryOut(BaseModel):
    repository_id: str
    project_id: str
    remote_url: str | None = None


class RepositoryCreate(BaseModel):
    repository_id: str
    project_id: str
    repository_name: str
    remote_url: str | None = None
    local_path: str | None = None
    default_branch: str = "main"
    is_active: bool = True
