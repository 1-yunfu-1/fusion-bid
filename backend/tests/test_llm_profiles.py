"""多组 API 配置（profiles）接口测试."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.core import llm_secrets as secrets


def _patch_secrets(tmp_path, monkeypatch):
    monkeypatch.setattr(secrets, "secrets_path", lambda: tmp_path / "llm_secrets.json")
    monkeypatch.setattr(
        "app.core.llm_secrets.get_settings",
        lambda: type("S", (), {"llm_api_key": "", "data_dir": tmp_path})(),
    )
    monkeypatch.setattr(
        "app.core.config.get_settings",
        lambda: type(
            "S",
            (),
            {
                "llm_api_key": "",
                "data_dir": tmp_path,
                "llm_enabled": True,
                "llm_base_url": "https://api.example.com/v1",
                "llm_model": "default-model",
                "llm_timeout": 30,
                "ollama_enabled": False,
                "ollama_base_url": "http://127.0.0.1:11434",
                "ollama_model": "x",
                "ollama_timeout": 60,
            },
        )(),
    )
    monkeypatch.setattr(
        "app.core.llm_runtime.runtime_path",
        lambda: tmp_path / "llm_runtime.json",
    )


@pytest.mark.asyncio
async def test_create_list_activate_delete_profiles(client: AsyncClient, tmp_path, monkeypatch):
    _patch_secrets(tmp_path, monkeypatch)

    # 创建第一组
    r1 = await client.post(
        "/api/llm/profiles",
        json={
            "name": "OpenAI",
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-openai-secret-key-001",
            "model": "gpt-4o-mini",
            "activate": True,
        },
    )
    assert r1.status_code == 200
    b1 = r1.json()
    assert b1["ok"] is True
    assert b1["profile"]["name"] == "OpenAI"
    assert "sk-openai-secret-key-001" not in r1.text
    assert b1["profile"]["key_hint"]
    pid1 = b1["profile"]["id"]

    # 创建第二组（不激活）
    r2 = await client.post(
        "/api/llm/profiles",
        json={
            "name": "DeepSeek",
            "base_url": "https://api.deepseek.com/v1",
            "api_key": "sk-deepseek-secret-key-002",
            "model": "deepseek-chat",
            "activate": False,
        },
    )
    assert r2.status_code == 200
    pid2 = r2.json()["profile"]["id"]

    # 列表
    lst = await client.get("/api/llm/profiles")
    assert lst.status_code == 200
    data = lst.json()
    assert data["count"] == 2
    assert data["active_profile_id"] == pid1
    assert "sk-openai" not in lst.text or "..." in str(data)

    # 切换到第二组
    act = await client.post(f"/api/llm/profiles/{pid2}/activate")
    assert act.status_code == 200
    assert act.json()["active_profile_id"] == pid2
    assert act.json()["profile"]["name"] == "DeepSeek"

    st = await client.get("/api/llm/status")
    api = st.json()["api"]
    assert api["active_profile_id"] == pid2
    assert api["base_url"] == "https://api.deepseek.com/v1"
    assert api["model"] == "deepseek-chat"
    assert "sk-deepseek-secret-key-002" not in st.text

    # 更新第二组名称
    up = await client.put(
        f"/api/llm/profiles/{pid2}",
        json={
            "name": "DeepSeek-生产",
            "base_url": "https://api.deepseek.com/v1",
            "model": "deepseek-chat",
            "activate": True,
        },
    )
    assert up.status_code == 200
    assert up.json()["profile"]["name"] == "DeepSeek-生产"
    assert up.json()["profile"]["key_configured"] is True  # key 未改

    # 删除
    d = await client.delete(f"/api/llm/profiles/{pid2}")
    assert d.status_code == 200
    assert d.json()["active_profile_id"] == pid1
    assert len(d.json()["profiles"]) == 1


@pytest.mark.asyncio
async def test_legacy_credentials_still_works(client: AsyncClient, tmp_path, monkeypatch):
    _patch_secrets(tmp_path, monkeypatch)
    r = await client.put(
        "/api/llm/credentials",
        json={"api_key": "sk-legacy-compat-key-9999"},
    )
    assert r.status_code == 200
    assert r.json()["configured"] is True
    assert "sk-legacy-compat-key-9999" not in r.text
    g = await client.get("/api/llm/profiles")
    assert g.json()["count"] >= 1
