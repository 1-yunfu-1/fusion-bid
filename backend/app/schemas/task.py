"""任务相关 schemas."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.intent import ParsedIntent, ValidationIssue


class TaskOut(BaseModel):
    id: str
    original_query: str
    parsed_intent: dict[str, Any] | None = None
    keywords: list[str] | None = None
    regions: list[str] | None = None
    start_date: date | None = None
    end_date: date | None = None
    execute_immediately: bool
    schedule_enabled: bool
    schedule_type: str | None = None
    execute_time: str | None = None
    execute_date: date | None = None
    is_paused: bool = False
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None
    timezone: str
    status: str
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class TaskListResponse(BaseModel):
    items: list[TaskOut]
    total: int


class TaskUpdateRequest(BaseModel):
    intent: ParsedIntent
    force: bool = False


class TaskUpdateResponse(BaseModel):
    task: TaskOut
    issues: list[ValidationIssue] = Field(default_factory=list)
    message: str
