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


class RunDetail(RunListItem):
    branch: str | None = None
    intent: str | None = None
    summary: str | None = None
    human: str | None = None


class RunEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    event_id: UUID
    event_type: str
    tool_name: str | None = None
    files_touched: list[str]
    redaction_status: str
    occurred_at: datetime | None = None
    received_at: datetime | None = None
    raw_payload: dict


class ProjectOverview(BaseModel):
    model_config = ConfigDict(extra="ignore")

    project_id: str
    project_name: str | None = None
    run_count: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    earliest: datetime | None = None
    latest_activity: datetime | None = None
