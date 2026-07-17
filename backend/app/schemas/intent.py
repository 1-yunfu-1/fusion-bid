"""自然语言意图解析相关 schemas."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class DateRangeSchema(BaseModel):
    start_date: date | None = None
    end_date: date | None = None
    original_expression: str | None = None


class ScheduleSchema(BaseModel):
    enabled: bool = False
    schedule_type: Literal["once", "daily", "weekly", "monthly"] | None = None
    execute_date: date | None = None
    execute_time: str | None = None  # HH:MM
    timezone: str = "Asia/Shanghai"


class ParsedIntent(BaseModel):
    """结构化意图（与赛题建议 JSON 对齐）."""

    original_query: str
    keywords: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)
    regions: list[str] = Field(default_factory=list)
    date_range: DateRangeSchema = Field(default_factory=DateRangeSchema)
    schedule: ScheduleSchema = Field(default_factory=ScheduleSchema)
    execute_immediately: bool = True

    @field_validator("keywords", "exclude_keywords", "regions", mode="before")
    @classmethod
    def _none_to_list(cls, value: Any) -> list:
        if value is None:
            return []
        return value


class ValidationIssue(BaseModel):
    code: str
    message: str
    field: str | None = None
    severity: Literal["error", "warning"] = "error"


class ParseRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000, description="用户自然语言输入")
    prefer_llm: bool | None = Field(
        default=None,
        description="是否优先使用大模型；默认跟随 LLM_ENABLED",
    )
    reference_time: datetime | None = Field(
        default=None,
        description="可选：解析参考时间（ISO），用于测试与回放；默认当前 Asia/Shanghai 时间",
    )


class ParseResponse(BaseModel):
    intent: ParsedIntent
    parser_used: Literal["api", "ollama", "rule", "hybrid", "llm"]
    llm_attempted: bool = False
    llm_success: bool = False
    llm_error: str | None = None
    issues: list[ValidationIssue] = Field(default_factory=list)
    needs_user_input: bool = False
    can_confirm: bool = True
    suggestions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ConfirmParseRequest(BaseModel):
    """人工确认/修改后的意图，用于创建任务."""

    intent: ParsedIntent
    force: bool = Field(
        default=False,
        description="存在 warning 时是否强制确认；error 不可强制",
    )


class ConfirmParseResponse(BaseModel):
    task_id: str
    status: str
    intent: ParsedIntent
    issues: list[ValidationIssue] = Field(default_factory=list)
    message: str
