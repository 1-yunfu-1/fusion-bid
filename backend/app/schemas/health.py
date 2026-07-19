"""健康检查与元信息 schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class PublicBrowserStatus(BaseModel):
    state: str = "not_started"
    engine: str | None = None
    profile_ready: bool = False
    last_error: str | None = None
    pool_size: int = 1
    active_workers: int = 0
    queue_size: int = 0
    adaptive_mode: bool = False
    pdf_pipeline: dict[str, Any] = Field(default_factory=dict)


class HealthResponse(BaseModel):
    status: str = Field(description="overall | ok / degraded / error")
    app: str
    version: str
    phase: str
    timezone: str
    time: datetime
    database: str = Field(description="ok | error")
    database_ok: bool
    database_revision: str = "unversioned"
    extraction_version: str = "v3"
    capabilities: list[str] = Field(default_factory=list)
    public_browser: PublicBrowserStatus = Field(default_factory=PublicBrowserStatus)
    message: str = ""


class MetaResponse(BaseModel):
    name: str
    version: str
    phase: str
    timezone: str
    language: str = "zh-CN"
    description: str
    features_ready: list[str]
    features_planned: list[str]
