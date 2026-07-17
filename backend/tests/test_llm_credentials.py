"""API Key 本地保存接口测试（不回显明文）."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.core import llm_secrets as secrets


@pytest.mark.asyncio
async def test_save_and_get_credentials(client: AsyncClient, tmp_path, monkeypatch):
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
                "llm_model": "m",
                "llm_timeout": 30,
                "ollama_enabled": False,
                "ollama_base_url": "http://127.0.0.1:11434",
                "ollama_model": "x",
                "ollama_timeout": 60,
            },
        )(),
    )

    # 保存
    r = await client.put(
        "/api/llm/credentials",
        json={"api_key": "sk-test-secret-key-123456"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["configured"] is True
    assert "sk-test-secret-key-123456" not in r.text
    assert body.get("hint")
    assert "sk-t" in body["hint"] or "..." in body["hint"]

    # 状态
    g = await client.get("/api/llm/credentials")
    assert g.status_code == 200
    assert g.json()["configured"] is True
    assert "sk-test-secret-key-123456" not in g.text

    # status 汇总
    st = await client.get("/api/llm/status")
    assert st.status_code == 200
    api = st.json()["api"]
    assert api["key_configured"] is True
    assert "sk-test-secret-key-123456" not in st.text

    # 清除
    c = await client.put("/api/llm/credentials", json={"clear": True})
    assert c.status_code == 200
    assert c.json()["configured"] is False


@pytest.mark.asyncio
async def test_credentials_reject_empty(client: AsyncClient):
    r = await client.put("/api/llm/credentials", json={"api_key": ""})
    assert r.status_code == 400
