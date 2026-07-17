"""兼容 API 模型探测与选择测试."""

from __future__ import annotations

import pytest
from httpx import AsyncClient, Response

from app.core.llm_runtime import load_runtime, save_runtime, LlmRuntimeConfig
from app.llm import client as llm_client


@pytest.mark.asyncio
async def test_api_list_models_without_key(client: AsyncClient):
    # 默认测试环境通常无 LLM_API_KEY
    resp = await client.get("/api/llm/models")
    assert resp.status_code == 200
    data = resp.json()
    assert "models" in data
    assert isinstance(data["models"], list)
    # 无 Key 时 ok=false 并提示；有 Key 则为探测结果
    if not data.get("ok"):
        msg = data.get("message", "")
        assert "KEY" in msg.upper() or "未配置" in msg or "未启用" in msg or "无法" in msg


@pytest.mark.asyncio
async def test_api_list_models_mocked(monkeypatch, tmp_path):
    monkeypatch.setattr(
        llm_client,
        "effective_llm_settings",
        lambda: {
            "prefer_order": ["api", "rule"],
            "api_enabled": True,
            "api_base_url": "https://fake.api/v1",
            "api_model": "gpt-4o-mini",
            "api_key_configured": True,
            "api_timeout": 30,
            "ollama_enabled": False,
            "ollama_base_url": "http://127.0.0.1:11434",
            "ollama_model": "x",
            "ollama_timeout": 60,
            "runtime_path": "data/llm_runtime.json",
        },
    )
    monkeypatch.setattr(
        "app.core.llm_secrets.get_llm_api_key",
        lambda: "sk-test-key",
    )

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def get(self, url, headers=None):
            assert url.endswith("/models")
            assert "Bearer sk-test-key" in (headers or {}).get("Authorization", "")
            return Response(
                200,
                json={
                    "data": [
                        {"id": "gpt-4o-mini", "owned_by": "openai"},
                        {"id": "gpt-4o", "owned_by": "openai"},
                        {"id": "deepseek-chat", "owned_by": "deepseek"},
                    ]
                },
            )

    monkeypatch.setattr(llm_client.httpx, "AsyncClient", FakeClient)

    result = await llm_client.api_list_models()
    assert result["ok"] is True
    assert result["count"] == 3
    ids = [m["id"] for m in result["models"]]
    assert "gpt-4o-mini" in ids
    assert "deepseek-chat" in ids
    assert "sk-test-key" not in str(result)


@pytest.mark.asyncio
async def test_select_api_model(client: AsyncClient, tmp_path, monkeypatch):
    from app.core import llm_runtime as rt

    monkeypatch.setattr(rt, "runtime_path", lambda: tmp_path / "llm_runtime.json")
    save_runtime(LlmRuntimeConfig())
    resp = await client.post("/api/llm/models/select", json={"model": "deepseek-chat"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["api_model"] == "deepseek-chat"
    assert load_runtime().api_model == "deepseek-chat"


@pytest.mark.asyncio
async def test_select_api_model_rejects_bad_name(client: AsyncClient):
    resp = await client.post("/api/llm/models/select", json={"model": "bad name with spaces!!!"})
    # spaces may fail regex
    assert resp.status_code in (400, 422)
