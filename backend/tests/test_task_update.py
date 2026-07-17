"""任务编辑 API 测试."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from httpx import AsyncClient


def _intent(
    *,
    query: str = "最近1个月安徽省服务器招标",
    keywords: list[str] | None = None,
    regions: list[str] | None = None,
    schedule_enabled: bool = False,
) -> dict:
    return {
        "original_query": query,
        "keywords": keywords or ["服务器"],
        "exclude_keywords": [],
        "regions": regions or ["安徽省"],
        "date_range": {
            "start_date": (date.today() - timedelta(days=30)).isoformat(),
            "end_date": date.today().isoformat(),
            "original_expression": "最近1个月",
        },
        "schedule": {
            "enabled": schedule_enabled,
            "schedule_type": "daily" if schedule_enabled else None,
            "execute_date": None,
            "execute_time": "09:00" if schedule_enabled else None,
            "timezone": "Asia/Shanghai",
        },
        "execute_immediately": not schedule_enabled,
    }


@pytest.mark.asyncio
async def test_update_task_keywords_and_regions(client: AsyncClient):
    created = await client.post("/api/parse/confirm", json={"intent": _intent()})
    assert created.status_code == 200, created.text
    task_id = created.json()["task_id"]

    body = {
        "intent": _intent(
            query="最近1个月北京市服务器招标信息",
            keywords=["服务器", "机架"],
            regions=["北京市"],
        ),
        "force": False,
    }
    r = await client.put(f"/api/tasks/{task_id}", json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["message"] == "任务已更新"
    assert data["task"]["keywords"] == ["服务器", "机架"]
    assert data["task"]["regions"] == ["北京市"]
    assert "北京市" in data["task"]["original_query"]

    g = await client.get(f"/api/tasks/{task_id}")
    assert g.status_code == 200
    assert g.json()["keywords"] == ["服务器", "机架"]


@pytest.mark.asyncio
async def test_update_deleted_task_rejected(client: AsyncClient):
    created = await client.post(
        "/api/parse/confirm",
        json={"intent": _intent(query="将要删除的任务", regions=["上海市"])},
    )
    assert created.status_code == 200, created.text
    task_id = created.json()["task_id"]

    d = await client.delete(f"/api/tasks/{task_id}")
    assert d.status_code == 200

    r = await client.put(
        f"/api/tasks/{task_id}",
        json={"intent": _intent(query="改", regions=["上海市"])},
    )
    assert r.status_code == 400
    assert "删除" in r.json()["detail"]
