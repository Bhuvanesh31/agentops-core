from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class RunListItem(BaseModel):
    model_config = ConfigDict(extra="ignore")  # run_overview has more columns than we expose

    run_id: UUID
    project_id: str
    project_name: str | None = None
    repository_id: str
    tool_id: str
    model: str | None = None
    session_id: str | None = None
    status: str
    started_at: datetime | None = None
    ended_at: datetime | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    cost_usd: Decimal | None = None
    cost_source: str | None = None
    iteration_count: int | None = None
    tool_calls_count: int | None = None
