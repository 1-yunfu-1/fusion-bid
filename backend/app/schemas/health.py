"""健康检查与元信息 schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = Field(description="overall | ok / degraded / error")
    app: str
    version: str
    phase: str
    timezone: str
    time: datetime
    database: str = Field(description="ok | error")
    database_ok: bool
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
