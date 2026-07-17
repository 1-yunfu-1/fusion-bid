"""LLM 运行时配置与状态接口（不调用真实外网）."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.core.llm_runtime import LlmRuntimeConfig, load_runtime, save_runtime, update_runtime


def test_runtime_roundtrip(tmp_path, monkeypatch):
    from app.core import llm_runtime as mod

    monkeypatch.setattr(mod, "runtime_path", lambda: tmp_path / "llm_runtime.json")
    cfg = save_runtime(
        LlmRuntimeConfig(
            prefer_order=["api", "ollama", "rule"],
            ollama_model="qwen2.5:3b",
            api_model="gpt-4o-mini",
        )
    )
    assert cfg.ollama_model == "qwen2.5:3b"
    loaded = load_runtime()
    assert loaded.api_model == "gpt-4o-mini"
    update_runtime(ollama_model="llama3.2:3b")
    assert load_runtime().ollama_model == "llama3.2:3b"


@pytest.mark.asyncio
async def test_llm_status_endpoint(client: AsyncClient):
    resp = await client.get("/api/llm/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "api" in data
    assert "ollama" in data
    assert "prefer_order" in data
    assert data["api"]["key_configured"] is False or isinstance(data["api"]["key_configured"], bool)


@pytest.mark.asyncio
async def test_update_runtime_via_api(client: AsyncClient, tmp_path, monkeypatch):
    from app.core import llm_runtime as mod

    monkeypatch.setattr(mod, "runtime_path", lambda: tmp_path / "llm_runtime.json")
    resp = await client.put(
        "/api/llm/runtime",
        json={
            "prefer_order": ["api", "ollama", "rule"],
            "ollama_model": "phi3:mini",
            "api_model": "deepseek-chat",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["runtime"]["ollama_model"] == "phi3:mini"
    assert body["runtime"]["api_model"] == "deepseek-chat"
