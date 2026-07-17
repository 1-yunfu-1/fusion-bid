"""LLM 状态与运行时配置 schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class LlmStatusResponse(BaseModel):
    prefer_order: list[str]
    api: dict[str, Any]
    ollama: dict[str, Any]
    runtime: dict[str, Any]
    notes: list[str] = Field(default_factory=list)


class LlmRuntimeUpdateRequest(BaseModel):
    prefer_order: list[str] | None = None
    api_model: str | None = None
    api_base_url: str | None = None
    ollama_model: str | None = None
    ollama_base_url: str | None = None
    api_enabled: bool | None = None
    ollama_enabled: bool | None = None


class OllamaPullRequest(BaseModel):
    model: str = Field(..., min_length=1, max_length=128, description="如 qwen2.5:3b")


class ApiModelSelectRequest(BaseModel):
    model: str = Field(..., min_length=1, max_length=256, description="兼容 API 模型 id")


class ApiModelsProbeRequest(BaseModel):
    """可选：指定 Base URL 探测（不改运行时配置）."""

    base_url: str | None = Field(
        default=None,
        description="临时探测用的 API Base URL，如 https://api.openai.com/v1；默认用当前配置",
    )


class ApiCredentialsRequest(BaseModel):
    """保存或清除 API Key（仅本地 secrets，不回显明文）."""

    api_key: str | None = Field(
        default=None,
        description="兼容 API 的 Bearer Key；传空字符串可配合 clear=true 清除",
    )
    clear: bool = Field(default=False, description="为 true 时清除本地保存的 Key")


class ApiProfileUpsertRequest(BaseModel):
    """新建或更新一组 API 配置（路径 + Key + 可选模型）."""

    name: str = Field(..., min_length=1, max_length=64, description="配置名称，便于识别")
    base_url: str = Field(
        default="",
        max_length=512,
        description="API Base URL，如 https://api.openai.com/v1",
    )
    api_key: str | None = Field(
        default=None,
        description="Bearer Key；更新时省略则不改动已有 Key",
    )
    model: str | None = Field(default=None, max_length=256, description="该配置默认模型 id")
    activate: bool = Field(default=True, description="保存后是否立即切换为当前配置")
    clear_key: bool = Field(default=False, description="更新时清除该配置的 Key")


class ApiProfileActivateRequest(BaseModel):
    profile_id: str = Field(..., min_length=1, max_length=64)
