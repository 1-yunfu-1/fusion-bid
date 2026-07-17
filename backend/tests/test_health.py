"""健康检查 API 测试."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_ok(client: AsyncClient):
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] in ("ok", "degraded")
    assert data["database_ok"] is True
    assert data["database"] == "ok"
    assert "Asia/Shanghai" in data["timezone"]
    assert data["phase"] == "phase8-integration"






    assert "智标" in data["app"] or "FusionBid" in data["app"]


@pytest.mark.asyncio
async def test_meta(client: AsyncClient):
    resp = await client.get("/api/meta")
    assert resp.status_code == 200
    data = resp.json()
    assert data["language"] == "zh-CN"
    assert data["timezone"] == "Asia/Shanghai"
    assert "项目骨架" in data["features_ready"]
    assert len(data["features_planned"]) >= 1


@pytest.mark.asyncio
async def test_root(client: AsyncClient):
    resp = await client.get("/")
    assert resp.status_code == 200
    data = resp.json()
    assert "health" in data
    assert data["health"] == "/api/health"
