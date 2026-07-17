"""LLM 状态、API/Ollama 模型探测与选择、多组 API 配置."""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException

from app.core.llm_runtime import effective_llm_settings, load_runtime, update_runtime
from app.core.llm_secrets import (
    activate_profile,
    clear_api_key,
    delete_profile,
    key_status,
    list_profiles,
    save_api_key,
    upsert_profile,
)
from app.llm.client import (
    api_health,
    api_list_models,
    ollama_health,
    ollama_list_models,
    ollama_pull_model,
)
from app.schemas.llm import (
    ApiCredentialsRequest,
    ApiModelSelectRequest,
    ApiModelsProbeRequest,
    ApiProfileUpsertRequest,
    LlmRuntimeUpdateRequest,
    LlmStatusResponse,
    OllamaPullRequest,
)

router = APIRouter(prefix="/llm", tags=["llm"])

_MODEL_ID_RE = re.compile(r"^[\w.\-:/+@]{1,256}$")


@router.get("/status", response_model=LlmStatusResponse)
async def llm_status() -> LlmStatusResponse:
    eff = effective_llm_settings()
    api = await api_health()
    ollama = await ollama_health()
    ks = key_status()
    notes = [
        "解析优先级：兼容 API → 本地 Ollama → 规则降级。",
        "可在设置页保存多组 API 配置（名称 + Base URL + Key + 模型），随时切换。",
        "密钥存 data/llm_secrets.json（已 gitignore）；也可用环境变量 LLM_API_KEY 兜底。",
        "本接口永不返回完整 Key，仅显示是否已配置与脱敏提示。",
        "可在设置页「探测 API 模型」列出兼容接口可用模型并选用。",
        "可在设置页选择 Ollama 已下载模型，或填写自定义模型名后拉取。",
    ]
    return LlmStatusResponse(
        prefer_order=eff["prefer_order"],
        api={
            "enabled": eff["api_enabled"],
            "base_url": eff["api_base_url"],
            "model": eff["api_model"],
            "key_configured": eff["api_key_configured"],
            "key_source": ks.get("source"),
            "key_hint": ks.get("hint") or "",
            "key_message": ks.get("message"),
            "active_profile_id": ks.get("active_profile_id"),
            "active_profile_name": ks.get("active_profile_name"),
            "profile_count": ks.get("profile_count") or 0,
            "timeout": eff["api_timeout"],
            "health": api,
        },
        ollama={
            "enabled": eff["ollama_enabled"],
            "base_url": eff["ollama_base_url"],
            "model": eff["ollama_model"],
            "timeout": eff["ollama_timeout"],
            "health": ollama,
        },
        runtime={
            "path": eff["runtime_path"],
            "current": load_runtime().model_dump(),
        },
        notes=notes,
    )


@router.get("/credentials")
async def get_credentials_status() -> dict:
    """查询 API Key / 配置组状态（脱敏，不含明文）."""
    return list_profiles()


@router.put("/credentials")
@router.post("/credentials")
async def put_credentials(body: ApiCredentialsRequest) -> dict:
    """兼容：填写 / 清除当前激活配置的 API Key.

    完整路径：PUT|POST /api/llm/credentials
    请求体：{"api_key":"sk-..."} 或 {"clear": true}
    响应不含完整 Key。推荐使用 /api/llm/profiles 管理多组配置。
    """
    if body.clear:
        status = clear_api_key()
        return {"ok": True, "action": "cleared", **status, "message": "已清除当前配置的 API Key"}
    key = (body.api_key or "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="请提供 api_key，或设置 clear=true")
    try:
        status = save_api_key(key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "ok": True,
        "action": "saved",
        **status,
        "message": "API Key 已保存到当前配置组（不会提交 Git，接口不回显明文）",
    }


@router.get("/profiles")
async def get_profiles() -> dict:
    """列出全部 API 配置组（Key 仅脱敏）."""
    return list_profiles()


@router.post("/profiles")
async def create_profile(body: ApiProfileUpsertRequest) -> dict:
    """新建一组 API 配置（名称 + Base URL + Key + 可选模型）."""
    try:
        result = upsert_profile(
            name=body.name,
            base_url=body.base_url or "",
            api_key=body.api_key,
            model=body.model,
            activate=body.activate,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # 激活时同步 runtime 的 base_url / model，便于界面立即一致
    if body.activate:
        kwargs: dict = {}
        if body.base_url:
            kwargs["api_base_url"] = body.base_url.strip().rstrip("/")
        if body.model:
            kwargs["api_model"] = body.model.strip()
        if kwargs:
            update_runtime(**kwargs)
    return {
        **result,
        "action": "created",
        "message": f"已保存配置「{body.name}」",
        "effective": effective_llm_settings(),
    }


@router.put("/profiles/{profile_id}")
async def update_profile(profile_id: str, body: ApiProfileUpsertRequest) -> dict:
    """更新已有配置；api_key 省略则不改密钥."""
    try:
        result = upsert_profile(
            profile_id=profile_id,
            name=body.name,
            base_url=body.base_url or "",
            api_key=body.api_key,
            model=body.model,
            activate=body.activate,
            clear_key=body.clear_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if body.activate or result.get("active_profile_id") == profile_id:
        kwargs: dict = {}
        if body.base_url is not None:
            kwargs["api_base_url"] = (body.base_url or "").strip().rstrip("/") or None
        if body.model is not None and body.model.strip():
            kwargs["api_model"] = body.model.strip()
        if kwargs:
            update_runtime(**kwargs)
    return {
        **result,
        "action": "updated",
        "message": f"已更新配置「{body.name}」",
        "effective": effective_llm_settings(),
    }


@router.post("/profiles/{profile_id}/activate")
async def activate_profile_route(profile_id: str) -> dict:
    """切换当前使用的 API 配置组，并同步 Base URL / 默认模型到 runtime."""
    try:
        result = activate_profile(profile_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    profile = result.get("profile") or {}
    kwargs: dict = {}
    if profile.get("base_url"):
        kwargs["api_base_url"] = profile["base_url"]
    if profile.get("model"):
        kwargs["api_model"] = profile["model"]
    if kwargs:
        update_runtime(**kwargs)
    return {
        **result,
        "action": "activated",
        "effective": effective_llm_settings(),
    }


@router.delete("/profiles/{profile_id}")
async def delete_profile_route(profile_id: str) -> dict:
    """删除一组 API 配置."""
    try:
        result = delete_profile(profile_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        **result,
        "action": "deleted",
        "message": "已删除配置",
        "effective": effective_llm_settings(),
    }


@router.get("/models")
async def get_api_models() -> dict:
    """探测当前配置的兼容 API 可用模型列表（OpenAI GET /v1/models）.

    完整路径：GET /api/llm/models
    """
    return await api_list_models()


@router.post("/models/probe")
async def probe_api_models(body: ApiModelsProbeRequest) -> dict:
    """可选指定 Base URL 探测模型（不修改运行时；Key 仍用环境变量）.

    完整路径：POST /api/llm/models/probe
    """
    return await api_list_models(base_url=body.base_url)


@router.post("/models/select")
async def select_api_model(body: ApiModelSelectRequest) -> dict:
    """选择兼容 API 当前使用的模型 id，写入 llm_runtime.json.

    完整路径：POST /api/llm/models/select
    """
    name = body.model.strip()
    if not name or not _MODEL_ID_RE.match(name):
        raise HTTPException(status_code=400, detail="非法模型名")
    cfg = update_runtime(api_model=name)
    return {
        "ok": True,
        "api_model": cfg.api_model,
        "effective": effective_llm_settings(),
        "message": f"已选择 API 模型：{name}",
    }


# 兼容旧路径（若前端缓存仍请求 /api/llm/api/*）
@router.get("/api/models", include_in_schema=False)
async def get_api_models_legacy() -> dict:
    return await api_list_models()


@router.post("/api/models/probe", include_in_schema=False)
async def probe_api_models_legacy(body: ApiModelsProbeRequest) -> dict:
    return await api_list_models(base_url=body.base_url)


@router.post("/api/select", include_in_schema=False)
async def select_api_model_legacy(body: ApiModelSelectRequest) -> dict:
    return await select_api_model(body)


@router.put("/runtime")
async def put_runtime(body: LlmRuntimeUpdateRequest) -> dict:
    """更新运行时模型与优先级（不含 API Key）."""
    payload = body.model_dump(exclude_unset=True)
    if "prefer_order" in payload and payload["prefer_order"] is not None:
        order = [x.lower().strip() for x in payload["prefer_order"]]
        allowed = {"api", "ollama", "rule"}
        if not order or any(x not in allowed for x in order):
            raise HTTPException(status_code=400, detail="prefer_order 仅允许 api/ollama/rule")
        if "rule" not in order:
            order.append("rule")
        payload["prefer_order"] = order
    cfg = update_runtime(**payload)
    return {"ok": True, "runtime": cfg.model_dump(), "effective": effective_llm_settings()}


@router.get("/ollama/models")
async def get_ollama_models() -> dict:
    eff = effective_llm_settings()
    health = await ollama_health()
    if not health["ok"]:
        return {"ok": False, "message": health["message"], "models": [], "base_url": eff["ollama_base_url"]}
    try:
        models = await ollama_list_models()
        return {
            "ok": True,
            "base_url": eff["ollama_base_url"],
            "selected": eff["ollama_model"],
            "models": models,
            "recommended": [
                {"name": "qwen2.5:3b", "note": "体积小，中文意图足够"},
                {"name": "qwen2.5:7b", "note": "效果更好，需更多内存"},
                {"name": "llama3.2:3b", "note": "通用小模型"},
                {"name": "phi3:mini", "note": "微软小模型"},
            ],
        }
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"列出模型失败: {exc}") from exc


@router.post("/ollama/pull")
async def pull_ollama_model(body: OllamaPullRequest) -> dict:
    """从 Ollama 库下载/拉取模型（耗时可能较长）."""
    try:
        result = await ollama_pull_model(body.model.strip())
        if result.get("ok"):
            # 拉取成功后可选设为当前模型
            update_runtime(ollama_model=body.model.strip())
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"拉取失败: {exc}") from exc


@router.post("/ollama/select")
async def select_ollama_model(body: OllamaPullRequest) -> dict:
    """选择已有或将要使用的 Ollama 模型名（不强制已下载）."""
    name = body.model.strip()
    if not name:
        raise HTTPException(status_code=400, detail="模型名不能为空")
    cfg = update_runtime(ollama_model=name)
    return {"ok": True, "ollama_model": cfg.ollama_model, "effective": effective_llm_settings()}
