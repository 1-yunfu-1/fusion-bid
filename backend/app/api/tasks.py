"""任务查询、更新与立即执行 API."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.models.execution import TaskExecution
from app.models.task import SearchTask
from app.parsers.validator import validate_intent
from app.schemas.task import TaskListResponse, TaskOut, TaskUpdateRequest, TaskUpdateResponse
from app.services.crawl_service import execute_search_task

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("", response_model=TaskListResponse)
async def list_tasks(
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
) -> TaskListResponse:
    limit = min(max(limit, 1), 200)
    offset = max(offset, 0)
    base = select(SearchTask).where(SearchTask.status != "deleted")
    total = await db.scalar(select(func.count()).select_from(base.subquery())) or 0
    result = await db.execute(
        base.order_by(SearchTask.created_at.desc()).offset(offset).limit(limit)
    )
    items = list(result.scalars().all())
    return TaskListResponse(items=[TaskOut.model_validate(t) for t in items], total=int(total))


@router.get("/{task_id}", response_model=TaskOut)
async def get_task(task_id: str, db: AsyncSession = Depends(get_db)) -> TaskOut:
    task = await db.get(SearchTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return TaskOut.model_validate(task)


@router.put("/{task_id}", response_model=TaskUpdateResponse)
async def update_task(
    task_id: str,
    body: TaskUpdateRequest,
    db: AsyncSession = Depends(get_db),
) -> TaskUpdateResponse:
    """编辑已创建任务的查询条件与调度配置（软删除任务不可编辑）."""
    task = await db.get(SearchTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.status == "deleted":
        raise HTTPException(status_code=400, detail="已删除的任务不可编辑")

    settings = get_settings()
    now = datetime.now(ZoneInfo(settings.app_timezone))
    intent = body.intent
    issues = validate_intent(intent, reference_time=now, timezone=settings.app_timezone)
    errors = [i for i in issues if i.severity == "error"]
    hard = [e for e in errors if e.code in ("expired_schedule", "conflicting_dates")]
    if hard:
        raise HTTPException(
            status_code=400,
            detail={"message": "存在必须修正的问题", "issues": [e.model_dump() for e in hard]},
        )
    if errors and not body.force:
        raise HTTPException(
            status_code=400,
            detail={"message": "仍有校验错误", "issues": [e.model_dump() for e in errors]},
        )

    schedule_enabled = bool(intent.schedule.enabled)
    if any(i.code == "expired_schedule" for i in issues):
        schedule_enabled = False

    task.original_query = intent.original_query
    task.parsed_intent = intent.model_dump(mode="json")
    task.keywords = list(intent.keywords or [])
    task.regions = list(intent.regions or [])
    task.start_date = intent.date_range.start_date
    task.end_date = intent.date_range.end_date
    task.execute_immediately = bool(intent.execute_immediately)
    task.schedule_enabled = schedule_enabled
    task.schedule_type = intent.schedule.schedule_type
    task.execute_time = intent.schedule.execute_time
    task.execute_date = intent.schedule.execute_date
    task.timezone = intent.schedule.timezone or settings.app_timezone
    # 编辑后若关闭定时，解除暂停标记；开启定时则保持原 is_paused（可再点恢复）
    if not schedule_enabled:
        task.is_paused = False
        task.status = "confirmed"
    else:
        task.status = "paused" if task.is_paused else "scheduled"

    await db.commit()
    await db.refresh(task)

    # 同步调度器
    try:
        from app.scheduler.manager import remove_job, schedule_task

        if task.schedule_enabled and not task.is_paused:
            info = schedule_task(task)
            if info.get("next_run_at"):
                task.next_run_at = datetime.fromisoformat(info["next_run_at"])
            await db.commit()
            await db.refresh(task)
        else:
            remove_job(task.id)
            task.next_run_at = None
            await db.commit()
            await db.refresh(task)
    except Exception:  # noqa: BLE001
        pass

    return TaskUpdateResponse(
        task=TaskOut.model_validate(task),
        issues=issues,
        message="任务已更新",
    )


@router.post("/{task_id}/execute")
async def execute_task(task_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    """立即执行多源采集（防并发；登录源失败不阻塞公开源）."""
    from app.scheduler import manager as sched

    task = await db.get(SearchTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.status == "deleted":
        raise HTTPException(status_code=400, detail="任务已删除")
    if not task.keywords or not task.regions:
        raise HTTPException(status_code=400, detail="任务缺少关键词或区域，请先确认意图")

    async with sched._run_lock:
        if task_id in sched._running_tasks:
            raise HTTPException(status_code=409, detail="该任务正在执行中，请勿重复触发")
        sched._running_tasks.add(task_id)
    try:
        execution, stats = await execute_search_task(db, task)
        task.last_run_at = datetime.now(ZoneInfo(task.timezone or "Asia/Shanghai"))
        await db.commit()
        return {
            "execution_id": execution.id,
            "task_id": task.id,
            "status": execution.status,
            "sources_requested": stats.sources_requested,
            "sources_succeeded": stats.sources_succeeded,
            "sources_failed": stats.sources_failed,
            "raw_result_count": stats.raw_result_count,
            "detail_success_count": stats.detail_success_count,
            "filtered_out_count": stats.filtered_out_count,
            "duplicate_count": stats.duplicate_count,
            "cross_source_merge_count": stats.cross_source_merge_count,
            "saved_count": stats.saved_count,
            "incremental_count": stats.incremental_count,
            "update_count": stats.update_count,
            "skipped_already_delivered": stats.skipped_already_delivered,
            "announcement_ids": stats.announcement_ids,
            "output_items": stats.output_items,
            "dedupe_reasons": stats.dedupe_reasons[:20],
            "report_path": stats.report_path or execution.report_path,
            "error_message": execution.error_message,
            "message": "采集完成" if execution.status != "failed" else "采集失败",
        }
    finally:
        async with sched._run_lock:
            sched._running_tasks.discard(task_id)


@router.post("/{task_id}/pause")
async def pause_task(task_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    task = await db.get(SearchTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    task.is_paused = True
    if task.schedule_enabled:
        task.status = "paused"
    from app.scheduler.manager import pause_job, remove_job

    pause_job(task_id)
    remove_job(task_id)
    task.next_run_at = None
    await db.commit()
    await db.refresh(task)
    return {"ok": True, "task": TaskOut.model_validate(task), "message": "任务已暂停"}


@router.post("/{task_id}/resume")
async def resume_task(task_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    task = await db.get(SearchTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if not task.schedule_enabled:
        raise HTTPException(status_code=400, detail="该任务未启用定时，无需恢复")
    task.is_paused = False
    task.status = "scheduled"
    try:
        from app.scheduler.manager import compute_next_run, schedule_task

        info = schedule_task(task)
        if info.get("next_run_at"):
            task.next_run_at = datetime.fromisoformat(info["next_run_at"])
        else:
            task.next_run_at = compute_next_run(
                schedule_type=task.schedule_type or "daily",
                execute_time=task.execute_time,
                execute_date=task.execute_date,
                timezone=task.timezone,
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        # 调度器未启动时仍更新状态
        from app.scheduler.manager import compute_next_run

        task.next_run_at = compute_next_run(
            schedule_type=task.schedule_type or "daily",
            execute_time=task.execute_time,
            execute_date=task.execute_date,
            timezone=task.timezone,
        )
        if task.next_run_at is None and task.schedule_type == "once":
            raise HTTPException(status_code=400, detail="单次任务已过期，无法恢复") from exc
    await db.commit()
    await db.refresh(task)
    return {"ok": True, "task": TaskOut.model_validate(task), "message": "任务已恢复"}


@router.delete("/{task_id}")
async def delete_task(task_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    task = await db.get(SearchTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    from app.scheduler.manager import remove_job

    remove_job(task_id)
    task.schedule_enabled = False
    task.is_paused = True
    task.status = "deleted"
    task.next_run_at = None
    await db.commit()
    return {"ok": True, "message": "任务已删除（软删除）", "task_id": task_id}


@router.get("/scheduler/jobs")
async def scheduler_jobs() -> dict:
    from app.scheduler.manager import get_scheduler, list_jobs

    return {
        "running": get_scheduler() is not None,
        "jobs": list_jobs(),
    }


@router.get("/{task_id}/executions")
async def list_executions(task_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    task = await db.get(SearchTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    rows = (
        await db.execute(
            select(TaskExecution)
            .where(TaskExecution.task_id == task_id)
            .order_by(TaskExecution.created_at.desc())
        )
    ).scalars().all()
    items = []
    for e in rows:
        items.append(
            {
                "id": e.id,
                "status": e.status,
                "started_at": e.started_at.isoformat() if e.started_at else None,
                "finished_at": e.finished_at.isoformat() if e.finished_at else None,
                "sources_requested": e.sources_requested,
                "sources_succeeded": e.sources_succeeded,
                "raw_result_count": e.raw_result_count,
                "filtered_result_count": e.filtered_result_count,
                "duplicate_count": e.duplicate_count,
                "incremental_count": e.incremental_count,
                "report_path": e.report_path,
                "error_message": e.error_message,
            }
        )
    return {"items": items, "total": len(items)}
