"""三则官方示例 + 调度示例的 API 级联调（规则解析，无外网抓取）."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

REF = "2026-07-17T08:00:00+08:00"


@pytest.mark.asyncio
async def test_official_case1_confirm(client: AsyncClient):
    q = "最近1个月的安徽省区域内的服务器招标信息都有哪些"
    r = await client.post(
        "/api/parse", json={"query": q, "prefer_llm": False, "reference_time": REF}
    )
    assert r.status_code == 200
    intent = r.json()["intent"]
    assert "安徽省" in intent["regions"]
    assert any("服务器" in k for k in intent["keywords"])
    assert intent["execute_immediately"] is True
    assert intent["schedule"]["enabled"] is False
    c = await client.post("/api/parse/confirm", json={"intent": intent})
    assert c.status_code == 200
    assert c.json()["task_id"]


@pytest.mark.asyncio
async def test_official_case2_march(client: AsyncClient):
    q = "2026年3月份的上海区域内的充电桩招标信息都有哪些"
    r = await client.post(
        "/api/parse", json={"query": q, "prefer_llm": False, "reference_time": REF}
    )
    intent = r.json()["intent"]
    assert intent["date_range"]["start_date"] == "2026-03-01"
    assert intent["date_range"]["end_date"] == "2026-03-31"
    assert "上海市" in intent["regions"]
    assert any("充电桩" in k for k in intent["keywords"])
    c = await client.post("/api/parse/confirm", json={"intent": intent})
    assert c.status_code == 200


@pytest.mark.asyncio
async def test_official_case3_daily_schedule(client: AsyncClient):
    q = "最近3个月的上海区域内的充电桩招标信息都有哪些，请汇总后每天9:00发送给我"
    r = await client.post(
        "/api/parse", json={"query": q, "prefer_llm": False, "reference_time": REF}
    )
    intent = r.json()["intent"]
    assert intent["schedule"]["enabled"] is True
    assert intent["schedule"]["schedule_type"] == "daily"
    assert intent["schedule"]["execute_time"] == "09:00"
    assert intent["execute_immediately"] is False
    c = await client.post("/api/parse/confirm", json={"intent": intent})
    assert c.status_code == 200
    assert c.json()["status"] in ("scheduled", "confirmed")


@pytest.mark.asyncio
async def test_sources_and_reports_endpoints(client: AsyncClient):
    s = await client.get("/api/sources")
    assert s.status_code == 200
    names = {i["source_name"] for i in s.json()["items"]}
    assert "ccgp" in names and "cebpub" in names and "login_portal" in names

    login = await client.get("/api/login/status")
    assert login.status_code == 200
    assert "instructions" in login.json()

    rep = await client.get("/api/reports")
    assert rep.status_code == 200
