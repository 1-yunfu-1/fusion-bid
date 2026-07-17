"""APScheduler 调度管理：持久化靠 DB，启动时从库恢复任务.

支持 once / daily / weekly / monthly；
防并发重复执行；过期 once 不调度。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.models.task import SearchTask

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None
_running_tasks: set[str] = set()
_run_lock = asyncio.Lock()


def job_id_for(task_id: str) -> str:
    return f"fusionbid_task_{task_id}"


def get_scheduler() -> AsyncIOScheduler | None:
    return _scheduler


def parse_hhmm(value: str | None) -> tuple[int, int]:
    if not value:
        return 9, 0
    parts = value.strip().split(":")
    hour = int(parts[0])
    minute = int(parts[1]) if len(parts) > 1 else 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"非法时间: {value}")
    return hour, minute


def compute_next_run(
    *,
    schedule_type: str,
    execute_time: str | None,
    execute_date: date | None,
    timezone: str,
    now: datetime | None = None,
) -> datetime | None:
    tz = ZoneInfo(timezone or "Asia/Shanghai")
    now = now or datetime.now(tz)
    if now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    else:
        now = now.astimezone(tz)
    hour, minute = parse_hhmm(execute_time)

    if schedule_type == "once":
        d = execute_date or now.date()
        run_at = datetime(d.year, d.month, d.day, hour, minute, tzinfo=tz)
        if run_at <= now:
            return None  # 过期
        return run_at

    # 下一次 daily/weekly/monthly 发生时间（粗算供展示）
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if schedule_type == "daily":
        if candidate <= now:
            candidate = candidate + timedelta(days=1)
        return candidate
    if schedule_type == "weekly":
        # 默认用 execute_date 的星期，否则周一
        target_wd = execute_date.weekday() if execute_date else 0
        days_ahead = (target_wd - now.weekday()) % 7
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(
            days=days_ahead
        )
        if candidate <= now:
            candidate = candidate + timedelta(days=7)
        return candidate
    if schedule_type == "monthly":
        day = execute_date.day if execute_date else 1
        day = min(day, 28)  # 简化避免月末问题
        year, month = now.year, now.month
        candidate = datetime(year, month, day, hour, minute, tzinfo=tz)
        if candidate <= now:
            if month == 12:
                year, month = year + 1, 1
            else:
                month += 1
            candidate = datetime(year, month, day, hour, minute, tzinfo=tz)
        return candidate
    return None


async def _execute_job(task_id: str) -> None:
    """调度回调：带并发锁执行采集（增量报告由 crawl_service 处理）."""
    async with _run_lock:
        if task_id in _running_tasks:
            logger.warning("skip concurrent run for task %s", task_id)
            return
        _running_tasks.add(task_id)

    try:
        from app.services.crawl_service import execute_search_task

        async with AsyncSessionLocal() as db:
            task = await db.get(SearchTask, task_id)
            if not task:
                logger.warning("scheduled task missing: %s", task_id)
                return
            if task.is_paused or not task.schedule_enabled:
                logger.info("task %s paused or schedule disabled, skip", task_id)
                return
            if task.status in ("deleted",):
                return

            logger.info("scheduled run start task=%s", task_id)
            execution, _stats = await execute_search_task(db, task)
            # 刷新 next_run / once 处理
            task = await db.get(SearchTask, task_id)
            if not task:
                return
            task.last_run_at = datetime.now(ZoneInfo(task.timezone or "Asia/Shanghai"))
            if task.schedule_type == "once":
                task.schedule_enabled = False
                task.status = "done"
                task.next_run_at = None
                remove_job(task_id)
            else:
                task.next_run_at = compute_next_run(
                    schedule_type=task.schedule_type or "daily",
                    execute_time=task.execute_time,
                    execute_date=task.execute_date,
                    timezone=task.timezone,
                )
                task.status = "scheduled"
            await db.commit()
            logger.info(
                "scheduled run finished task=%s status=%s",
                task_id,
                execution.status,
            )
    except Exception:  # noqa: BLE001
        logger.exception("scheduled job failed task=%s", task_id)
    finally:
        async with _run_lock:
            _running_tasks.discard(task_id)


def _build_trigger(task: SearchTask):
    tz = task.timezone or "Asia/Shanghai"
    hour, minute = parse_hhmm(task.execute_time)
    st = (task.schedule_type or "daily").lower()

    if st == "once":
        next_run = compute_next_run(
            schedule_type="once",
            execute_time=task.execute_time,
            execute_date=task.execute_date,
            timezone=tz,
        )
        if next_run is None:
            raise ValueError("单次任务执行时间已过期，无法调度")
        return DateTrigger(run_date=next_run, timezone=tz)

    if st == "daily":
        return CronTrigger(hour=hour, minute=minute, timezone=tz)

    if st == "weekly":
        # APScheduler: mon=0 ... sun=6 in cron? Actually day_of_week mon-sun
        wd = task.execute_date.weekday() if task.execute_date else 0
        # cron: 0=mon ... 6=sun in APScheduler
        return CronTrigger(day_of_week=wd, hour=hour, minute=minute, timezone=tz)

    if st == "monthly":
        day = task.execute_date.day if task.execute_date else 1
        day = min(max(day, 1), 28)
        return CronTrigger(day=day, hour=hour, minute=minute, timezone=tz)

    raise ValueError(f"不支持的 schedule_type: {st}")


def schedule_task(task: SearchTask, *, replace: bool = True) -> dict[str, Any]:
    """将任务注册到调度器."""
    if _scheduler is None:
        raise RuntimeError("调度器未启动")
    if not task.schedule_enabled or task.is_paused:
        remove_job(task.id)
        return {"scheduled": False, "reason": "未启用或已暂停"}

    jid = job_id_for(task.id)
    trigger = _build_trigger(task)
    _scheduler.add_job(
        _execute_job,
        trigger=trigger,
        id=jid,
        args=[task.id],
        replace_existing=replace,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )
    job = _scheduler.get_job(jid)
    next_run = job.next_run_time if job else None
    return {
        "scheduled": True,
        "job_id": jid,
        "next_run_at": next_run.isoformat() if next_run else None,
    }


def remove_job(task_id: str) -> bool:
    if _scheduler is None:
        return False
    jid = job_id_for(task_id)
    try:
        _scheduler.remove_job(jid)
        return True
    except Exception:  # noqa: BLE001
        return False


def pause_job(task_id: str) -> bool:
    if _scheduler is None:
        return False
    try:
        _scheduler.pause_job(job_id_for(task_id))
        return True
    except Exception:  # noqa: BLE001
        return False


def resume_job(task_id: str) -> bool:
    if _scheduler is None:
        return False
    try:
        _scheduler.resume_job(job_id_for(task_id))
        return True
    except Exception:  # noqa: BLE001
        return False


def list_jobs() -> list[dict[str, Any]]:
    if _scheduler is None:
        return []
    out = []
    for job in _scheduler.get_jobs():
        out.append(
            {
                "id": job.id,
                "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
                "pending": getattr(job, "pending", None),
            }
        )
    return out


async def restore_jobs_from_db() -> int:
    """进程启动后从 DB 恢复未暂停的定时任务."""
    count = 0
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(SearchTask).where(
                    SearchTask.schedule_enabled.is_(True),
                    SearchTask.is_paused.is_(False),
                    SearchTask.status.in_(["scheduled", "confirmed", "done"]),
                )
            )
        ).scalars().all()
        for task in rows:
            if task.status == "done" and task.schedule_type == "once":
                continue
            try:
                info = schedule_task(task)
                if info.get("scheduled"):
                    task.next_run_at = (
                        datetime.fromisoformat(info["next_run_at"])
                        if info.get("next_run_at")
                        else compute_next_run(
                            schedule_type=task.schedule_type or "daily",
                            execute_time=task.execute_time,
                            execute_date=task.execute_date,
                            timezone=task.timezone,
                        )
                    )
                    if task.status != "scheduled":
                        task.status = "scheduled"
                    count += 1
            except ValueError as exc:
                logger.warning("skip restore task %s: %s", task.id, exc)
                task.schedule_enabled = False
                if task.schedule_type == "once":
                    task.status = "expired"
            except Exception:  # noqa: BLE001
                logger.exception("restore failed task=%s", task.id)
        await db.commit()
    return count


def start_scheduler() -> AsyncIOScheduler:
    global _scheduler
    settings = get_settings()
    if _scheduler is not None:
        return _scheduler
    _scheduler = AsyncIOScheduler(timezone=settings.app_timezone)
    _scheduler.start()
    logger.info("APScheduler started tz=%s", settings.app_timezone)
    return _scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("APScheduler stopped")
