"""运行时 LLM 偏好（用户可选模型，不写入密钥到仓库）。

优先级：环境变量默认值 < data/llm_runtime.json 中的用户选择。
API Key 不写入本文件；见 data/llm_secrets.json 或环境变量 LLM_API_KEY。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.core.config import PROJECT_ROOT, get_settings

_lock = threading.Lock()


class LlmRuntimeConfig(BaseModel):
    """可被前端/API 修改的运行时选项."""

    # 解析优先级：api → ollama → rule
    prefer_order: list[str] = Field(default_factory=lambda: ["api", "ollama", "rule"])
    # 云端/兼容 API 模型名（覆盖环境变量 LLM_MODEL）
    api_model: str | None = None
    # 可选：覆盖 API Base URL（不含 Key）
    api_base_url: str | None = None
    # Ollama 模型名（覆盖 OLLAMA_MODEL）
    ollama_model: str | None = None
    # Ollama 服务地址
    ollama_base_url: str | None = None
    # 是否启用各通道（仍受 LLM_ENABLED / OLLAMA_ENABLED 总开关约束）
    api_enabled: bool | None = None
    ollama_enabled: bool | None = None


def runtime_path() -> Path:
    settings = get_settings()
    path = settings.data_dir / "llm_runtime.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def load_runtime() -> LlmRuntimeConfig:
    path = runtime_path()
    if not path.exists():
        return LlmRuntimeConfig()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        # 安全：若误写入 key 字段则丢弃
        data.pop("api_key", None)
        data.pop("llm_api_key", None)
        return LlmRuntimeConfig.model_validate(data)
    except Exception:
        return LlmRuntimeConfig()


def save_runtime(cfg: LlmRuntimeConfig) -> LlmRuntimeConfig:
    path = runtime_path()
    payload: dict[str, Any] = cfg.model_dump()
    payload.pop("api_key", None)
    with _lock:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg


def update_runtime(**kwargs: Any) -> LlmRuntimeConfig:
    current = load_runtime()
    data = current.model_dump()
    for k, v in kwargs.items():
        if k in ("api_key", "llm_api_key"):
            continue
        if v is not None or k in data:
            data[k] = v
    return save_runtime(LlmRuntimeConfig.model_validate(data))


def effective_llm_settings() -> dict[str, Any]:
    """合并环境变量、激活 profile 与运行时偏好（不含明文 Key）.

    API Base/Model 优先级：runtime 覆盖 > 激活 profile > 环境变量默认。
    """
    from app.core.llm_secrets import (
        get_active_api_base_url,
        get_active_api_model,
        get_llm_api_key,
        key_status,
    )

    s = get_settings()
    r = load_runtime()
    order = r.prefer_order or ["api", "ollama", "rule"]
    # 规范化
    order = [x.lower().strip() for x in order if x]
    if "rule" not in order:
        order.append("rule")

    api_enabled = s.llm_enabled if r.api_enabled is None else (s.llm_enabled and r.api_enabled)
    ollama_enabled = (
        s.ollama_enabled if r.ollama_enabled is None else (s.ollama_enabled and r.ollama_enabled)
    )
    ks = key_status()
    profile_base = get_active_api_base_url()
    profile_model = get_active_api_model()

    return {
        "prefer_order": order,
        "api_enabled": api_enabled,
        "api_base_url": (r.api_base_url or profile_base or s.llm_base_url).rstrip("/"),
        "api_model": r.api_model or profile_model or s.llm_model,
        "api_key_configured": bool(get_llm_api_key()),
        "api_key_source": ks.get("source"),
        "api_key_hint": ks.get("hint") or "",
        "active_profile_id": ks.get("active_profile_id"),
        "active_profile_name": ks.get("active_profile_name"),
        "profile_count": ks.get("profile_count") or 0,
        "api_timeout": s.llm_timeout,
        "ollama_enabled": ollama_enabled,
        "ollama_base_url": (r.ollama_base_url or s.ollama_base_url).rstrip("/"),
        "ollama_model": r.ollama_model or s.ollama_model,
        "ollama_timeout": s.ollama_timeout,
        "runtime_path": str(runtime_path().relative_to(PROJECT_ROOT))
        if runtime_path().is_relative_to(PROJECT_ROOT)
        else str(runtime_path()),
    }
