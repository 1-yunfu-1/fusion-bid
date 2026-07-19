"""任务查询、更新与立即执行 API."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.models.execution import TaskExecution
from app.models.task import SearchTask
from app.parsers.regions import resolve_region_selection
from app.parsers.validator import validate_intent
from app.schemas.task import (
    TaskExecuteRequest,
    TaskExecutionItem,
    TaskExecutionListResponse,
    TaskExecutionResponse,
    TaskListResponse,
    TaskOut,
    TaskUpdateRequest,
    TaskUpdateResponse,
)
from app.reports.analysis import analysis_preview
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
    intent.regions = resolve_region_selection(intent.regions).requested
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


def _report_fields(report_path: str | None) -> tuple[str | None, str | None]:
    if not report_path:
        return None, None
    path = Path(report_path)
    filename = path.name
    if not filename or not path.is_file():
        return filename or None, None
    return filename, f"/api/reports/download/{quote(filename)}"


@router.post("/{task_id}/execute", response_model=TaskExecutionResponse)
async def execute_task(
    task_id: str,
    body: TaskExecuteRequest | None = None,
    db: AsyncSession = Depends(get_db),
) -> TaskExecutionResponse:
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
        trigger_type = body.trigger_type if body else "manual"
        report_mode = body.report_mode if body else "incremental"
        report_scope = "snapshot" if report_mode == "full_snapshot" else "incremental"
        search_depth = body.search_depth if body else "standard"
        refresh_extraction = body.refresh_extraction if body else False
        execution, stats = await execute_search_task(
            db,
            task,
            trigger_type=trigger_type,
            report_mode=report_mode,
            report_scope=report_scope,
            search_depth=search_depth,
            refresh_extraction=refresh_extraction,
        )
        # 接口语义以当次请求为准；同时保留 report_scope 供旧客户端读取。
        execution.report_mode = report_mode
        execution.report_scope = report_scope
        execution.deduplicate = report_mode != "full_snapshot"
        execution.truncated = getattr(stats, "truncated", False)
        task.last_run_at = datetime.now(ZoneInfo(task.timezone or "Asia/Shanghai"))
        await db.commit()
        await db.refresh(task)
        report_filename, report_download_url = _report_fields(
            stats.report_path or execution.report_path
        )
        return TaskExecutionResponse(
            execution_id=execution.id,
            task_id=task.id,
            status=execution.status,
            task_status=task.status,
            trigger_type=execution.trigger_type,
            report_scope=execution.report_scope,
            report_mode=report_mode,
            deduplicate=getattr(stats, "deduplicate", report_mode != "full_snapshot"),
            truncated=getattr(stats, "truncated", False),
            next_run_at=task.next_run_at,
            sources_requested=stats.sources_requested,
            sources_succeeded=stats.sources_succeeded,
            sources_failed=stats.sources_failed,
            raw_result_count=stats.raw_result_count,
            detail_success_count=stats.detail_success_count,
            detail_metadata_only_count=stats.detail_metadata_only_count,
            detail_failed_count=(
                getattr(stats, "detail_failed", 0)
                + getattr(stats, "detail_status_failed_count", 0)
            ),
            detail_human_verification_count=getattr(
                stats, "detail_human_verification_count", 0
            ),
            detail_not_attempted_count=getattr(
                stats, "detail_not_attempted_count", 0
            ),
            cached_full_reused_count=getattr(
                stats, "cached_full_reused_count", 0
            ),
            failure_breakdown=getattr(stats, "failure_breakdown", {}),
            failure_breakdown_by_source=getattr(
                stats, "failure_breakdown_by_source", {}
            ),
            source_detail_breakdown=getattr(
                stats, "source_detail_breakdown", {}
            ),
            stage_durations_ms=getattr(stats, "stage_durations_ms", {}),
            effective_concurrency=getattr(stats, "effective_concurrency", {}),
            requested_regions=getattr(stats, "requested_regions", []),
            effective_regions=getattr(stats, "effective_regions", []),
            region_scope=getattr(stats, "region_scope", "restricted"),
            detail_cap=getattr(stats, "detail_cap", 30),
            detail_cap_skipped=getattr(stats, "detail_cap_skipped", 0),
            coverage_status=getattr(stats, "coverage_status", "complete"),
            search_depth=getattr(stats, "search_depth", search_depth),
            extraction_cache_hit_count=getattr(
                stats, "extraction_cache_hit_count", 0
            ),
            llm_call_count=getattr(stats, "llm_call_count", 0),
            llm_timeout_count=getattr(stats, "llm_timeout_count", 0),
            opportunity_count=getattr(stats, "opportunity_count", 0),
            lifecycle_count=getattr(stats, "lifecycle_count", 0),
            source_outcomes=getattr(stats, "source_outcomes", {}),
            filtered_out_count=stats.filtered_out_count,
            duplicate_count=stats.duplicate_count,
            cross_source_merge_count=stats.cross_source_merge_count,
            saved_count=stats.saved_count,
            incremental_count=stats.incremental_count,
            update_count=stats.update_count,
            skipped_already_delivered=stats.skipped_already_delivered,
            announcement_ids=stats.announcement_ids,
            output_items=stats.output_items,
            dedupe_reasons=stats.dedupe_reasons[:20],
            report_filename=report_filename,
            report_download_url=report_download_url,
            analysis_status=(stats.analysis_data or execution.analysis_data or {}).get("status", "rule_only"),
            analysis_provider=(stats.analysis_data or execution.analysis_data or {}).get("provider", "rules"),
            analysis_preview=analysis_preview(stats.analysis_data or execution.analysis_data),
            error_message=execution.error_message,
            message="采集完成" if execution.status != "failed" else "采集失败",
        )
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


@router.get("/{task_id}/executions", response_model=TaskExecutionListResponse)
async def list_executions(
    task_id: str, db: AsyncSession = Depends(get_db)
) -> TaskExecutionListResponse:
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
    items: list[TaskExecutionItem] = []
    for e in rows:
        report_filename, report_download_url = _report_fields(e.report_path)
        diagnostics = getattr(e, "crawl_diagnostics", None) or {}
        items.append(
            TaskExecutionItem(
                id=e.id,
                status=e.status,
                trigger_type=e.trigger_type,
                report_scope=e.report_scope,
                report_mode=getattr(e, "report_mode", None)
                or ("full_snapshot" if e.report_scope == "snapshot" else "incremental"),
                deduplicate=getattr(e, "deduplicate", e.report_scope != "snapshot"),
                truncated=getattr(e, "truncated", False),
                started_at=e.started_at,
                finished_at=e.finished_at,
                sources_requested=e.sources_requested or [],
                sources_succeeded=e.sources_succeeded or [],
                raw_result_count=e.raw_result_count,
                filtered_result_count=e.filtered_result_count,
                duplicate_count=e.duplicate_count,
                incremental_count=e.incremental_count,
                detail_full_count=getattr(e, "detail_full_count", 0),
                detail_metadata_count=getattr(e, "detail_metadata_count", 0),
                detail_failed_count=getattr(e, "detail_failed_count", 0),
                detail_human_verification_count=getattr(
                    e, "detail_human_verification_count", 0
                ),
                detail_not_attempted_count=int(
                    diagnostics.get("detail_not_attempted_count") or 0
                ),
                cached_full_reused_count=int(
                    diagnostics.get("cached_full_reused_count") or 0
                ),
                failure_breakdown=dict(diagnostics.get("failure_breakdown") or {}),
                failure_breakdown_by_source=dict(
                    diagnostics.get("failure_breakdown_by_source") or {}
                ),
                source_detail_breakdown=dict(
                    diagnostics.get("source_detail_breakdown") or {}
                ),
                stage_durations_ms=dict(diagnostics.get("stage_durations_ms") or {}),
                effective_concurrency=dict(
                    diagnostics.get("effective_concurrency") or {}
                ),
                requested_regions=list(diagnostics.get("requested_regions") or []),
                effective_regions=list(diagnostics.get("effective_regions") or []),
                region_scope=str(diagnostics.get("region_scope") or "restricted"),
                detail_cap=int(
                    diagnostics.get("detail_cap")
                    or getattr(e, "detail_cap", 30)
                    or 30
                ),
                detail_cap_skipped=int(
                    diagnostics.get("detail_cap_skipped")
                    or getattr(e, "detail_cap_skipped", 0)
                    or 0
                ),
                coverage_status=str(
                    diagnostics.get("coverage_status")
                    or getattr(e, "coverage_status", "complete")
                ),
                search_depth=str(
                    diagnostics.get("search_depth")
                    or getattr(e, "search_depth", "standard")
                ),
                extraction_cache_hit_count=int(
                    diagnostics.get("extraction_cache_hit_count")
                    or getattr(e, "extraction_cache_hit_count", 0)
                    or 0
                ),
                llm_call_count=int(
                    diagnostics.get("llm_call_count")
                    or getattr(e, "llm_call_count", 0)
                    or 0
                ),
                llm_timeout_count=int(
                    diagnostics.get("llm_timeout_count")
                    or getattr(e, "llm_timeout_count", 0)
                    or 0
                ),
                opportunity_count=int(
                    diagnostics.get("opportunity_count")
                    or getattr(e, "opportunity_count", 0)
                    or 0
                ),
                lifecycle_count=int(
                    diagnostics.get("lifecycle_count")
                    or getattr(e, "lifecycle_count", 0)
                    or 0
                ),
                source_outcomes=dict(diagnostics.get("source_outcomes") or {}),
                report_filename=report_filename,
                report_download_url=report_download_url,
                analysis_status=(e.analysis_data or {}).get("status", "rule_only"),
                analysis_provider=(e.analysis_data or {}).get("provider", "rules"),
                analysis_preview=analysis_preview(e.analysis_data),
                error_message=e.error_message,
            )
        )
    return TaskExecutionListResponse(items=items, total=len(items))
