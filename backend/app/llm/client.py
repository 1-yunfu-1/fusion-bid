"""双模式 LLM 客户端：优先兼容 API，其次 Ollama，结构化意图解析."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

import httpx

from app.core.llm_runtime import effective_llm_settings
from app.llm.prompts import INTENT_SYSTEM_PROMPT, build_user_prompt
from app.schemas.intent import ParsedIntent

logger = logging.getLogger(__name__)

ProviderName = Literal["api", "ollama"]


@dataclass
class LlmCallResult:
    success: bool
    provider: ProviderName | None = None
    model: str | None = None
    intent: ParsedIntent | None = None
    error: str | None = None
    raw_text: str | None = None


@dataclass
class JsonLlmCallResult:
    """Generic JSON call result for evidence-bounded post-processing."""

    success: bool
    provider: ProviderName | None = None
    model: str | None = None
    data: dict[str, Any] | None = None
    error: str | None = None
    error_kind: str | None = None


def _extract_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            raise
        return json.loads(m.group(0))


def _normalize_intent_payload(data: dict[str, Any], original_query: str) -> dict[str, Any]:
    """容错：补齐嵌套结构."""
    data = dict(data)
    data.setdefault("original_query", original_query)
    dr = data.get("date_range") or {}
    if not isinstance(dr, dict):
        dr = {}
    data["date_range"] = {
        "start_date": dr.get("start_date"),
        "end_date": dr.get("end_date"),
        "original_expression": dr.get("original_expression"),
    }
    sch = data.get("schedule") or {}
    if not isinstance(sch, dict):
        sch = {}
    data["schedule"] = {
        "enabled": bool(sch.get("enabled", False)),
        "schedule_type": sch.get("schedule_type"),
        "execute_date": sch.get("execute_date"),
        "execute_time": sch.get("execute_time"),
        "timezone": sch.get("timezone") or "Asia/Shanghai",
    }
    data.setdefault("keywords", [])
    data.setdefault("exclude_keywords", [])
    data.setdefault("regions", [])
    data.setdefault("execute_immediately", not data["schedule"]["enabled"])
    return data


async def _chat_completions(
    *,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    timeout: float,
    provider: ProviderName,
) -> str:
    """OpenAI 兼容 chat/completions."""
    url = f"{base_url.rstrip('/')}/chat/completions"
    # Ollama 的 /v1 前缀
    if provider == "ollama" and not base_url.rstrip("/").endswith("/v1"):
        url = f"{base_url.rstrip('/')}/v1/chat/completions"

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.1,
    }
    # 部分云端支持 json object；Ollama 新版本也可能支持
    if provider == "api":
        payload["response_format"] = {"type": "json_object"}

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, headers=headers, json=payload)
        # 若 API 不支持 response_format，去掉重试一次
        if resp.status_code >= 400 and provider == "api" and "response_format" in payload:
            payload.pop("response_format", None)
            resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        body = resp.json()
        return body["choices"][0]["message"]["content"]


async def parse_intent_with_provider(
    query: str,
    *,
    reference_time: datetime,
    provider: ProviderName,
) -> LlmCallResult:
    eff = effective_llm_settings()
    messages = [
        {"role": "system", "content": INTENT_SYSTEM_PROMPT},
        {"role": "user", "content": build_user_prompt(query, reference_time)},
    ]

    if provider == "api":
        if not eff["api_enabled"]:
            return LlmCallResult(success=False, provider="api", error="API 通道未启用")
        if not eff["api_key_configured"]:
            return LlmCallResult(
                success=False,
                provider="api",
                error="未配置 API Key（设置页填写或环境变量 LLM_API_KEY）",
            )
        base_url = eff["api_base_url"]
        model = eff["api_model"]
        from app.core.llm_secrets import get_llm_api_key

        api_key = get_llm_api_key()
        timeout = float(eff["api_timeout"])
    else:
        if not eff["ollama_enabled"]:
            return LlmCallResult(success=False, provider="ollama", error="Ollama 通道未启用")
        base_url = eff["ollama_base_url"]
        model = eff["ollama_model"]
        api_key = "ollama"
        timeout = float(eff["ollama_timeout"])

    try:
        text = await _chat_completions(
            base_url=base_url,
            api_key=api_key,
            model=model,
            messages=messages,
            timeout=timeout,
            provider=provider,
        )
        data = _normalize_intent_payload(_extract_json_object(text), query)
        intent = ParsedIntent.model_validate(data)
        intent.original_query = query
        return LlmCallResult(
            success=True,
            provider=provider,
            model=model,
            intent=intent,
            raw_text=text,
        )
    except Exception as exc:  # noqa: BLE001 — 通道失败需降级
        # 绝不把 key 写入日志
        msg = str(exc)
        msg = re.sub(r"Bearer\s+\S+", "Bearer ***", msg)
        logger.warning("LLM provider %s failed: %s", provider, msg)
        return LlmCallResult(success=False, provider=provider, model=model, error=msg)


async def parse_intent_llm_chain(
    query: str,
    *,
    reference_time: datetime,
    prefer_order: list[str] | None = None,
) -> LlmCallResult:
    """按优先级尝试 api → ollama."""
    eff = effective_llm_settings()
    order = prefer_order or [x for x in eff["prefer_order"] if x in ("api", "ollama")]
    last = LlmCallResult(success=False, error="无可用 LLM 通道")
    for name in order:
        if name not in ("api", "ollama"):
            continue
        result = await parse_intent_with_provider(
            query, reference_time=reference_time, provider=name  # type: ignore[arg-type]
        )
        if result.success:
            return result
        last = result
    return last


async def call_json_with_provider(
    messages: list[dict[str, str]], *, provider: ProviderName
) -> JsonLlmCallResult:
    """Call an enabled provider without exposing configuration secrets to callers."""
    eff = effective_llm_settings()
    if provider == "api":
        if not eff["api_enabled"] or not eff["api_key_configured"]:
            return JsonLlmCallResult(success=False, provider=provider, error="API unavailable")
        from app.core.llm_secrets import get_llm_api_key

        base_url = eff["api_base_url"]
        model = eff["api_model"]
        api_key = get_llm_api_key()
        timeout = float(eff["api_timeout"])
    else:
        if not eff["ollama_enabled"]:
            return JsonLlmCallResult(success=False, provider=provider, error="Ollama unavailable")
        base_url = eff["ollama_base_url"]
        model = eff["ollama_model"]
        api_key = "ollama"
        timeout = float(eff["ollama_timeout"])
    try:
        text = await _chat_completions(
            base_url=base_url,
            api_key=api_key,
            model=model,
            messages=messages,
            timeout=timeout,
            provider=provider,
        )
        data = _extract_json_object(text)
        return JsonLlmCallResult(
            success=True, provider=provider, model=model, data=data
        )
    except Exception as exc:  # noqa: BLE001
        message = re.sub(r"Bearer\s+\S+", "Bearer ***", str(exc))
        logger.info("Evidence-bounded LLM analysis unavailable for %s: %s", provider, message)
        return JsonLlmCallResult(
            success=False,
            provider=provider,
            model=model,
            error=message,
            error_kind=(
                "timeout"
                if isinstance(exc, (httpx.TimeoutException, TimeoutError))
                else "provider_error"
            ),
        )


async def call_json_llm_chain(
    messages: list[dict[str, str]],
    *,
    prefer_order: list[str] | None = None,
    stop_after_timeout: bool = False,
) -> JsonLlmCallResult:
    eff = effective_llm_settings()
    order = prefer_order or [x for x in eff["prefer_order"] if x in ("api", "ollama")]
    last = JsonLlmCallResult(success=False, error="No enabled LLM provider")
    for name in order:
        if name not in ("api", "ollama"):
            continue
        result = await call_json_with_provider(messages, provider=name)  # type: ignore[arg-type]
        if result.success:
            return result
        last = result
        if stop_after_timeout and result.error_kind == "timeout":
            break
    return last


# --- Ollama 管理：列表 / 健康 / 拉取 ---


async def ollama_health(base_url: str | None = None) -> dict[str, Any]:
    eff = effective_llm_settings()
    base = (base_url or eff["ollama_base_url"]).rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{base}/api/tags")
            resp.raise_for_status()
            data = resp.json()
            models = [m.get("name", "") for m in data.get("models", [])]
            return {
                "ok": True,
                "base_url": base,
                "models": models,
                "message": f"已连接，本地模型 {len(models)} 个",
            }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "base_url": base,
            "models": [],
            "message": f"无法连接 Ollama: {exc}",
        }


async def ollama_list_models(base_url: str | None = None) -> list[dict[str, Any]]:
    eff = effective_llm_settings()
    base = (base_url or eff["ollama_base_url"]).rstrip("/")
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{base}/api/tags")
        resp.raise_for_status()
        data = resp.json()
        out = []
        for m in data.get("models", []):
            out.append(
                {
                    "name": m.get("name"),
                    "size": m.get("size"),
                    "modified_at": m.get("modified_at"),
                    "digest": (m.get("digest") or "")[:16],
                }
            )
        return out


async def ollama_pull_model(model: str, base_url: str | None = None) -> dict[str, Any]:
    """触发拉取；流式响应聚合为最终状态（可能较久）."""
    if not model or not re.match(r"^[\w.\-:/]+$", model):
        raise ValueError("非法模型名")
    eff = effective_llm_settings()
    base = (base_url or eff["ollama_base_url"]).rstrip("/")
    timeout = httpx.Timeout(connect=10.0, read=600.0, write=30.0, pool=30.0)
    last_status = "starting"
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream(
            "POST",
            f"{base}/api/pull",
            json={"name": model, "stream": True},
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                    last_status = chunk.get("status") or last_status
                    if chunk.get("error"):
                        return {"ok": False, "model": model, "status": chunk["error"]}
                except json.JSONDecodeError:
                    continue
    return {"ok": True, "model": model, "status": last_status}


def _normalize_api_base(base: str) -> str:
    """统一为 .../v1 根路径，避免重复拼接."""
    b = (base or "").rstrip("/")
    return b


async def api_list_models(
    *,
    base_url: str | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """探测兼容 API 上可用的模型列表（GET /models，OpenAI 兼容）.

    返回结构中不含 API Key。
    """
    from app.core.llm_secrets import get_llm_api_key

    eff = effective_llm_settings()
    api_key = get_llm_api_key()
    base = _normalize_api_base(base_url or eff["api_base_url"])
    selected = eff["api_model"]

    if not eff["api_enabled"] and base_url is None:
        return {
            "ok": False,
            "base_url": base,
            "selected": selected,
            "models": [],
            "count": 0,
            "message": "API 通道未启用（可在设置中开启）",
        }
    if not api_key:
        return {
            "ok": False,
            "base_url": base,
            "selected": selected,
            "models": [],
            "count": 0,
            "message": "未配置 API Key，请在设置页填写或配置环境变量 LLM_API_KEY",
        }

    url = f"{base}/models"
    t = timeout if timeout is not None else min(float(eff["api_timeout"]), 30.0)
    try:
        async with httpx.AsyncClient(timeout=t) as client:
            resp = await client.get(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )
        if resp.status_code >= 400:
            # 脱敏后返回错误信息
            detail = resp.text[:200].replace(api_key, "***")
            return {
                "ok": False,
                "base_url": base,
                "selected": selected,
                "models": [],
                "count": 0,
                "http_status": resp.status_code,
                "message": f"探测失败 HTTP {resp.status_code}: {detail}",
            }

        body = resp.json()
        raw_list: list[Any]
        if isinstance(body, dict) and isinstance(body.get("data"), list):
            raw_list = body["data"]
        elif isinstance(body, list):
            raw_list = body
        else:
            raw_list = []

        models: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in raw_list:
            if isinstance(item, str):
                mid = item
                meta: dict[str, Any] = {"id": mid}
            elif isinstance(item, dict):
                mid = str(item.get("id") or item.get("name") or "").strip()
                meta = {
                    "id": mid,
                    "owned_by": item.get("owned_by"),
                    "created": item.get("created"),
                    "object": item.get("object"),
                }
                # 部分网关带 model 字段
                if not mid and item.get("model"):
                    mid = str(item["model"])
                    meta["id"] = mid
            else:
                continue
            if not mid or mid in seen:
                continue
            seen.add(mid)
            models.append(meta)

        # 按 id 排序，便于选择
        models.sort(key=lambda m: m.get("id") or "")
        return {
            "ok": True,
            "base_url": base,
            "selected": selected,
            "models": models,
            "count": len(models),
            "http_status": resp.status_code,
            "message": f"探测成功，共 {len(models)} 个模型",
        }
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if api_key:
            msg = msg.replace(api_key, "***")
        return {
            "ok": False,
            "base_url": base,
            "selected": selected,
            "models": [],
            "count": 0,
            "message": f"无法连接 API: {msg}",
        }


async def api_health() -> dict[str, Any]:
    """探测兼容 API（调用模型列表端点，不发送业务内容）."""
    result = await api_list_models()
    return {
        "ok": result["ok"],
        "base_url": result.get("base_url"),
        "model": result.get("selected"),
        "model_count": result.get("count", 0),
        "http_status": result.get("http_status"),
        "message": result.get("message"),
    }
