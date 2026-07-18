"""任务执行 API：触发类型、调度状态与并发保护."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient

from app.models.execution import TaskExecution

TZ = ZoneInfo("Asia/Shanghai")


def _intent(*, scheduled: bool = True) -> dict:
    today = date.today()
    return {
        "original_query": "最近一个月安徽省服务器招标",
        "keywords": ["服务器"],
        "exclude_keywords": [],
        "regions": ["安徽省"],
        "date_range": {
            "start_date": (today - timedelta(days=30)).isoformat(),
            "end_date": today.isoformat(),
            "original_expression": "最近一个月",
        },
        "schedule": {
            "enabled": scheduled,
            "schedule_type": "daily" if scheduled else None,
            "execute_date": None,
            "execute_time": "09:00" if scheduled else None,
            "timezone": "Asia/Shanghai",
        },
        "execute_immediately": True,
    }


def _stats():
    return SimpleNamespace(
        sources_requested=["mock_public", "mock_login"],
        sources_succeeded=["mock_public"],
        sources_failed={"mock_login": "未登录"},
        raw_result_count=2,
        detail_success_count=1,
        detail_metadata_only_count=0,
        filtered_out_count=0,
        duplicate_count=0,
        cross_source_merge_count=0,
        saved_count=1,
        incremental_count=1,
        update_count=0,
        skipped_already_delivered=0,
        announcement_ids=["a1"],
        output_items=[],
        dedupe_reasons=[],
        report_path=None,
        analysis_data={"status": "rule_only", "provider": "rules", "projects": []},
    )


@pytest.mark.asyncio
async def test_initial_execute_once_and_history_contract(client: AsyncClient, monkeypatch):
    created = await client.post("/api/parse/confirm", json={"intent": _intent()})
    assert created.status_code == 200, created.text
    task_id = created.json()["task_id"]
    calls: list[str] = []

    async def fake_execute(db, task, *, trigger_type="manual", report_scope="incremental", **kwargs):
        calls.append(trigger_type)
        execution = TaskExecution(
            task_id=task.id,
            trigger_type=trigger_type,
            report_scope=report_scope,
            status="partial",
            started_at=datetime.now(TZ),
            finished_at=datetime.now(TZ),
            sources_requested=["mock_public", "mock_login"],
            sources_succeeded=["mock_public"],
            raw_result_count=2,
            filtered_result_count=1,
            duplicate_count=0,
            incremental_count=1,
            error_message="mock_login: 未登录",
        )
        db.add(execution)
        task.status = "scheduled"
        await db.flush()
        return execution, _stats()

    monkeypatch.setattr("app.api.tasks.execute_search_task", fake_execute)
    response = await client.post(
        f"/api/tasks/{task_id}/execute", json={"trigger_type": "initial", "report_scope": "snapshot"}
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert calls == ["initial"]
    assert data["trigger_type"] == "initial"
    assert data["report_scope"] == "snapshot"
    assert data["task_status"] == "scheduled"
    assert data["sources_failed"] == {"mock_login": "未登录"}
    assert "report_path" not in data

    history = await client.get(f"/api/tasks/{task_id}/executions")
    assert history.status_code == 200
    item = history.json()["items"][0]
    assert item["trigger_type"] == "initial"
    assert "report_path" not in item


@pytest.mark.asyncio
async def test_duplicate_trigger_returns_409(client: AsyncClient):
    created = await client.post(
        "/api/parse/confirm", json={"intent": _intent(scheduled=False)}
    )
    task_id = created.json()["task_id"]
    from app.scheduler import manager

    manager._running_tasks.add(task_id)
    try:
        response = await client.post(
            f"/api/tasks/{task_id}/execute", json={"trigger_type": "manual"}
        )
        assert response.status_code == 409
    finally:
        manager._running_tasks.discard(task_id)
