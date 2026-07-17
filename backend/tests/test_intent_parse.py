"""意图解析验收案例（规则路径，禁止硬编码答案表）."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient

from app.parsers.rule_parser import parse_intent_by_rules
from app.parsers.service import parse_user_query
from app.parsers.validator import validate_intent

TZ = ZoneInfo("Asia/Shanghai")
# 固定参考时间：2026-07-17 10:30，用于可重复测试
REF = datetime(2026, 7, 17, 10, 30, tzinfo=TZ)
REF_MORNING = datetime(2026, 7, 17, 8, 0, tzinfo=TZ)


@pytest.mark.asyncio
async def test_case1_immediate_anhui_server():
    q = "最近1个月的安徽省区域内的服务器招标信息都有哪些"
    intent = parse_intent_by_rules(q, reference_time=REF)
    assert "安徽省" in intent.regions
    assert any("服务器" in k for k in intent.keywords)
    assert intent.date_range.start_date is not None
    assert intent.date_range.end_date == date(2026, 7, 17)
    assert intent.date_range.original_expression and "月" in intent.date_range.original_expression
    assert intent.schedule.enabled is False
    assert intent.execute_immediately is True
    issues = validate_intent(intent, reference_time=REF)
    assert not any(i.severity == "error" for i in issues)


@pytest.mark.asyncio
async def test_case2_march_shanghai_charger():
    q = "2026年3月份的上海区域内的充电桩招标信息都有哪些"
    intent = parse_intent_by_rules(q, reference_time=REF)
    assert "上海市" in intent.regions
    assert any("充电桩" in k for k in intent.keywords)
    assert intent.date_range.start_date == date(2026, 3, 1)
    assert intent.date_range.end_date == date(2026, 3, 31)
    assert intent.execute_immediately is True
    assert intent.schedule.enabled is False


@pytest.mark.asyncio
async def test_case3_daily_schedule():
    q = "最近3个月的上海区域内的充电桩招标信息都有哪些，请汇总后每天9:00发送给我"
    intent = parse_intent_by_rules(q, reference_time=REF)
    assert "上海市" in intent.regions
    assert any("充电桩" in k for k in intent.keywords)
    assert intent.date_range.start_date is not None
    assert intent.schedule.enabled is True
    assert intent.schedule.schedule_type == "daily"
    assert intent.schedule.execute_time == "09:00"
    assert intent.execute_immediately is False


@pytest.mark.asyncio
async def test_case4_today_9_expired():
    q = "2026年4月份上海的充电桩招标信息都有哪些，请汇总后今天9:00发送给我"
    # 当前 10:30 > 今天 9:00
    intent = parse_intent_by_rules(q, reference_time=REF)
    assert intent.schedule.enabled is True
    assert intent.schedule.schedule_type == "once"
    assert intent.schedule.execute_time == "09:00"
    issues = validate_intent(intent, reference_time=REF)
    assert any(i.code == "expired_schedule" for i in issues)


@pytest.mark.asyncio
async def test_case4_today_9_not_expired():
    q = "2026年4月份上海的充电桩招标信息都有哪些，请汇总后今天9:00发送给我"
    intent = parse_intent_by_rules(q, reference_time=REF_MORNING)
    issues = validate_intent(intent, reference_time=REF_MORNING)
    assert not any(i.code == "expired_schedule" for i in issues)
    assert intent.schedule.enabled is True


@pytest.mark.asyncio
async def test_case5_flexible_beijing_april():
    q = "帮我看看北京四月份有哪些和充电设施建设有关的招标，整理成报告"
    intent = parse_intent_by_rules(q, reference_time=REF)
    assert "北京市" in intent.regions
    assert intent.date_range.start_date == date(2026, 4, 1)
    assert intent.date_range.end_date == date(2026, 4, 30)
    assert any("充电" in k for k in intent.keywords)
    assert intent.schedule.enabled is False
    assert intent.execute_immediately is True


@pytest.mark.asyncio
async def test_missing_region_and_keyword():
    intent = parse_intent_by_rules("最近有什么招标", reference_time=REF)
    issues = validate_intent(intent, reference_time=REF)
    codes = {i.code for i in issues}
    assert "missing_regions" in codes
    assert "missing_keywords" in codes or "missing_date_range" in codes


@pytest.mark.asyncio
async def test_service_falls_back_to_rule_without_llm(monkeypatch):
    async def _fail(*_a, **_k):
        from app.llm.client import LlmCallResult

        return LlmCallResult(success=False, error="disabled in test")

    monkeypatch.setattr("app.parsers.service.parse_intent_llm_chain", _fail)
    resp = await parse_user_query(
        "最近1个月的安徽省区域内的服务器招标信息都有哪些",
        reference_time=REF,
        prefer_llm=True,
    )
    assert resp.parser_used == "rule"
    assert resp.llm_attempted is True
    assert resp.llm_success is False
    assert "安徽省" in resp.intent.regions


@pytest.mark.asyncio
async def test_api_parse_endpoint(client: AsyncClient):
    resp = await client.post(
        "/api/parse",
        json={
            "query": "最近1个月的安徽省区域内的服务器招标信息都有哪些",
            "prefer_llm": False,
            "reference_time": REF.isoformat(),
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["parser_used"] == "rule"
    assert "安徽省" in data["intent"]["regions"]
    assert data["intent"]["execute_immediately"] is True


@pytest.mark.asyncio
async def test_api_confirm_creates_task(client: AsyncClient):
    parse = await client.post(
        "/api/parse",
        json={
            "query": "2026年3月份的上海区域内的充电桩招标信息都有哪些",
            "prefer_llm": False,
            "reference_time": REF.isoformat(),
        },
    )
    intent = parse.json()["intent"]
    conf = await client.post("/api/parse/confirm", json={"intent": intent})
    assert conf.status_code == 200
    body = conf.json()
    assert body["task_id"]
    assert body["status"] in ("confirmed", "scheduled")

    listed = await client.get("/api/tasks")
    assert listed.status_code == 200
    assert listed.json()["total"] >= 1


@pytest.mark.asyncio
async def test_api_confirm_rejects_expired_once(client: AsyncClient):
    parse = await client.post(
        "/api/parse",
        json={
            "query": "2026年4月份上海的充电桩招标信息都有哪些，请汇总后今天9:00发送给我",
            "prefer_llm": False,
            "reference_time": REF.isoformat(),
        },
    )
    intent = parse.json()["intent"]
    conf = await client.post("/api/parse/confirm", json={"intent": intent, "force": True})
    # 过期 once 属于 hard error，即使 force 也拒绝保持 schedule
    assert conf.status_code == 400
