"""调度逻辑单元测试（不依赖真实爬网）."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.scheduler.manager import compute_next_run, parse_hhmm

TZ = ZoneInfo("Asia/Shanghai")


def test_parse_hhmm():
    assert parse_hhmm("09:00") == (9, 0)
    assert parse_hhmm("17:30") == (17, 30)


def test_once_future():
    now = datetime(2026, 7, 17, 8, 0, tzinfo=TZ)
    nxt = compute_next_run(
        schedule_type="once",
        execute_time="09:00",
        execute_date=date(2026, 7, 17),
        timezone="Asia/Shanghai",
        now=now,
    )
    assert nxt is not None
    assert nxt.hour == 9


def test_once_expired():
    now = datetime(2026, 7, 17, 10, 0, tzinfo=TZ)
    nxt = compute_next_run(
        schedule_type="once",
        execute_time="09:00",
        execute_date=date(2026, 7, 17),
        timezone="Asia/Shanghai",
        now=now,
    )
    assert nxt is None


def test_daily_next():
    now = datetime(2026, 7, 17, 10, 0, tzinfo=TZ)
    nxt = compute_next_run(
        schedule_type="daily",
        execute_time="09:00",
        execute_date=None,
        timezone="Asia/Shanghai",
        now=now,
    )
    assert nxt is not None
    assert nxt.date() == date(2026, 7, 18)
    assert nxt.hour == 9


def test_weekly_and_monthly():
    now = datetime(2026, 7, 17, 10, 0, tzinfo=TZ)  # Friday
    w = compute_next_run(
        schedule_type="weekly",
        execute_time="09:00",
        execute_date=date(2026, 7, 13),  # Monday
        timezone="Asia/Shanghai",
        now=now,
    )
    assert w is not None
    assert w.weekday() == 0

    m = compute_next_run(
        schedule_type="monthly",
        execute_time="09:00",
        execute_date=date(2026, 7, 1),
        timezone="Asia/Shanghai",
        now=now,
    )
    assert m is not None
    assert m.day == 1
    assert m.month == 8


@pytest.mark.asyncio
async def test_pause_resume_delete_api(client):
    # 创建任务
    parse = await client.post(
        "/api/parse",
        json={
            "query": "最近1个月的安徽省区域内的服务器招标信息都有哪些，请汇总后每天9:00发送给我",
            "prefer_llm": False,
            "reference_time": "2026-07-17T08:00:00+08:00",
        },
    )
    intent = parse.json()["intent"]
    conf = await client.post("/api/parse/confirm", json={"intent": intent})
    assert conf.status_code == 200
    task_id = conf.json()["task_id"]

    paused = await client.post(f"/api/tasks/{task_id}/pause")
    assert paused.status_code == 200
    assert paused.json()["task"]["is_paused"] is True

    resumed = await client.post(f"/api/tasks/{task_id}/resume")
    assert resumed.status_code == 200
    assert resumed.json()["task"]["is_paused"] is False

    deleted = await client.delete(f"/api/tasks/{task_id}")
    assert deleted.status_code == 200

    listed = await client.get("/api/tasks")
    ids = [t["id"] for t in listed.json()["items"]]
    assert task_id not in ids
