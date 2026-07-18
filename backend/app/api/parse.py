"""意图解析 API."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.models.task import SearchTask
from app.parsers.service import parse_user_query
from app.parsers.validator import validate_intent
from app.schemas.intent import ConfirmParseRequest, ConfirmParseResponse, ParseRequest, ParseResponse

router = APIRouter(prefix="/parse", tags=["parse"])


def _resolve_reference_time(value: datetime | None) -> datetime:
    settings = get_settings()
    tz = ZoneInfo(settings.app_timezone)
    if value is None:
        return datetime.now(tz)
    if value.tzinfo is None:
        return value.replace(tzinfo=tz)
    return value.astimezone(tz)


@router.post("", response_model=ParseResponse)
async def parse_query(body: ParseRequest) -> ParseResponse:
    """自然语言意图解析（API → Ollama → 规则）."""
    if not body.query.strip():
        raise HTTPException(status_code=400, detail="查询内容不能为空")
    ref = _resolve_reference_time(body.reference_time)
    return await parse_user_query(
        body.query.strip(),
        reference_time=ref,
        prefer_llm=body.prefer_llm,
    )


@router.post("/confirm", response_model=ConfirmParseResponse)
async def confirm_parse(
    body: ConfirmParseRequest,
    db: AsyncSession = Depends(get_db),
) -> ConfirmParseResponse:
    """人工确认/修改意图后创建 SearchTask."""
    settings = get_settings()
    now = datetime.now(ZoneInfo(settings.app_timezone))
    intent = body.intent
    intent.original_query = intent.original_query.strip()
    issues = validate_intent(intent, reference_time=now, timezone=settings.app_timezone)
    errors = [i for i in issues if i.severity == "error"]
    # 过期定时、冲突日期：禁止确认（含 force），不得静默创建过期任务
    hard = [e for e in errors if e.code in ("expired_schedule", "conflicting_dates")]
    if hard:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "存在必须修正的问题，无法确认（如过期执行时间）。请改为立即执行或有效时间。",
                "issues": [e.model_dump() for e in hard],
            },
        )
    if errors and not body.force:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "解析结果仍有错误，请修改后重试，或检查 issues",
                "issues": [e.model_dump() for e in errors],
            },
        )

    schedule_enabled = intent.schedule.enabled

    # 定时任务始终保留 scheduled 语义；execute_immediately 仅表示是否执行创建后的首轮。
    status = "scheduled" if schedule_enabled else "confirmed"

    task = SearchTask(
        original_query=intent.original_query,
        parsed_intent=intent.model_dump(mode="json"),
        keywords=intent.keywords,
        regions=intent.regions,
        start_date=intent.date_range.start_date,
        end_date=intent.date_range.end_date,
        execute_immediately=intent.execute_immediately,
        schedule_enabled=schedule_enabled,
        schedule_type=intent.schedule.schedule_type,
        execute_time=intent.schedule.execute_time,
        execute_date=intent.schedule.execute_date,
        is_paused=False,
        timezone=intent.schedule.timezone or settings.app_timezone,
        status=status,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)

    schedule_info = None
    if task.schedule_enabled and not task.is_paused:
        try:
            from app.scheduler.manager import compute_next_run, schedule_task

            info = schedule_task(task)
            schedule_info = info
            if info.get("next_run_at"):
                task.next_run_at = datetime.fromisoformat(info["next_run_at"])
            else:
                task.next_run_at = compute_next_run(
                    schedule_type=task.schedule_type or "daily",
                    execute_time=task.execute_time,
                    execute_date=task.execute_date,
                    timezone=task.timezone,
                )
            await db.commit()
            await db.refresh(task)
        except Exception as exc:  # noqa: BLE001
            # 调度失败不阻断任务创建
            schedule_info = {"scheduled": False, "error": str(exc)}

    msg = "任务已创建"
    if schedule_enabled:
        msg += "，已注册定时调度" if schedule_info and schedule_info.get("scheduled") else "（定时注册见 schedule_info）"
    if intent.execute_immediately:
        msg += "；将立即执行首轮检索"

    return ConfirmParseResponse(
        task_id=task.id,
        status=task.status,
        intent=intent,
        issues=issues,
        message=msg,
    )
