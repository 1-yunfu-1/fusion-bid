"""任务相关 schemas."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

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


class TaskExecuteRequest(BaseModel):
    trigger_type: Literal["initial", "manual"] = "manual"
    report_mode: Literal["incremental", "full_snapshot"] = "incremental"
    # 旧前端兼容；snapshot 等价于 full_snapshot。
    report_scope: Literal["incremental", "snapshot"] | None = None

    @model_validator(mode="before")
    @classmethod
    def _upgrade_legacy_scope(cls, value: Any) -> Any:
        if isinstance(value, dict) and "report_mode" not in value:
            upgraded = dict(value)
            upgraded["report_mode"] = (
                "full_snapshot"
                if upgraded.get("report_scope") == "snapshot"
                else "incremental"
            )
            return upgraded
        return value


class TaskExecutionResponse(BaseModel):
    execution_id: str
    task_id: str
    status: str
    task_status: str
    trigger_type: str
    report_scope: str = "incremental"
    report_mode: str = "incremental"
    deduplicate: bool = True
    truncated: bool = False
    next_run_at: datetime | None = None
    sources_requested: list[str] = Field(default_factory=list)
    sources_succeeded: list[str] = Field(default_factory=list)
    sources_failed: dict[str, str] = Field(default_factory=dict)
    raw_result_count: int = 0
    detail_success_count: int = 0
    detail_metadata_only_count: int = 0
    detail_failed_count: int = 0
    detail_human_verification_count: int = 0
    detail_not_attempted_count: int = 0
    failure_breakdown: dict[str, int] = Field(default_factory=dict)
    stage_durations_ms: dict[str, int] = Field(default_factory=dict)
    effective_concurrency: dict[str, Any] = Field(default_factory=dict)
    filtered_out_count: int = 0
    duplicate_count: int = 0
    cross_source_merge_count: int = 0
    saved_count: int = 0
    incremental_count: int = 0
    update_count: int = 0
    skipped_already_delivered: int = 0
    announcement_ids: list[str] = Field(default_factory=list)
    output_items: list[dict[str, Any]] = Field(default_factory=list)
    dedupe_reasons: list[str] = Field(default_factory=list)
    report_filename: str | None = None
    report_download_url: str | None = None
    analysis_status: str = "rule_only"
    analysis_provider: str = "rules"
    analysis_preview: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = None
    message: str


class TaskExecutionItem(BaseModel):
    id: str
    status: str
    trigger_type: str
    report_scope: str = "incremental"
    report_mode: str = "incremental"
    deduplicate: bool = True
    truncated: bool = False
    started_at: datetime | None = None
    finished_at: datetime | None = None
    sources_requested: list[str] = Field(default_factory=list)
    sources_succeeded: list[str] = Field(default_factory=list)
    raw_result_count: int = 0
    filtered_result_count: int = 0
    duplicate_count: int = 0
    incremental_count: int = 0
    detail_full_count: int = 0
    detail_metadata_count: int = 0
    detail_failed_count: int = 0
    detail_human_verification_count: int = 0
    detail_not_attempted_count: int = 0
    failure_breakdown: dict[str, int] = Field(default_factory=dict)
    stage_durations_ms: dict[str, int] = Field(default_factory=dict)
    effective_concurrency: dict[str, Any] = Field(default_factory=dict)
    report_filename: str | None = None
    report_download_url: str | None = None
    analysis_status: str = "rule_only"
    analysis_provider: str = "rules"
    analysis_preview: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = None


class TaskExecutionListResponse(BaseModel):
    items: list[TaskExecutionItem]
    total: int
