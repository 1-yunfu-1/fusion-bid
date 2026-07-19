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
    assert data["extraction_version"] == "v2"
    assert "interactive-detail-recrawl-v1" in data["capabilities"]
    assert "official-document-import-v1" in data["capabilities"]
    assert "pdfjs-text-layer-capture-v1" in data["capabilities"]
    assert "browser-rendered-detail-capture-v1" in data["capabilities"]
    assert "managed-public-browser-v1" in data["capabilities"]
    assert "managed-public-browser-pool-v2" in data["capabilities"]
    assert "pdfjs-memory-document-capture-v1" in data["capabilities"]
    assert data["public_browser"]["state"] in {
        "not_started",
        "starting",
        "ready",
        "busy",
        "needs_verification",
        "unavailable",
    }
    assert "port" not in data["public_browser"]
    assert "profile_dir" not in data["public_browser"]
    assert data["public_browser"]["pool_size"] == 2
    assert data["public_browser"]["active_workers"] == 0
    pipeline = data["public_browser"]["pdf_pipeline"]
    assert pipeline["memory_pdf_bytes"] is True
    assert isinstance(pipeline["text_ready"], bool)
    assert isinstance(pipeline["scanned_pdf_ready"], bool)
    assert pipeline["viewer_ready_timeout_seconds"] == 12
    assert pipeline["ocr_timeout_seconds"] == 60
    assert pipeline["invalid_pdf_cooldown_hours"] == 24
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
