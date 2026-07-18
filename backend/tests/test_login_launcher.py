"""登录启动器 API 测试（不真正打开浏览器 UI 断言结构）."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient

from app.services import login_launcher as launcher


@pytest.mark.asyncio
async def test_login_status_has_launcher(client: AsyncClient):
    r = await client.get("/api/login/status")
    assert r.status_code == 200
    data = r.json()
    assert "launcher" in data
    assert "process_running" in data["launcher"]
    assert "instructions" in data


@pytest.mark.asyncio
async def test_login_start_mocked(client: AsyncClient, monkeypatch):
    mock_proc = MagicMock()
    mock_proc.pid = 12345
    mock_proc.poll.return_value = None

    def fake_popen(*a, **k):
        return mock_proc

    monkeypatch.setattr(launcher.subprocess, "Popen", fake_popen)
    # reset module state
    launcher._process = None
    launcher._started_at = None

    r = await client.post(
        "/api/login/start",
        json={
            "login_url": "https://www.chinabidding.cn/",
            "wait_seconds": 120,
            "force": True,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body.get("pid") == 12345

    st = await client.get("/api/login/status")
    assert st.json()["launcher"]["process_running"] is True

    stop = await client.post("/api/login/stop")
    assert stop.status_code == 200


@pytest.mark.asyncio
async def test_clear_login_state(client: AsyncClient, tmp_path, monkeypatch):
    # clear_login_state 使用 login_launcher 模块内绑定的 state_file_path
    monkeypatch.setattr(
        launcher,
        "state_file_path",
        lambda filename=None: tmp_path / "login_portal_state.json",
    )
    f = tmp_path / "login_portal_state.json"
    f.write_text("{}", encoding="utf-8")
    r = await client.delete("/api/login/state")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["cleared"] is True
    assert not f.exists()
