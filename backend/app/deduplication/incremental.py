"""增量推送：基于 DeliveryHistory，失败执行不标记已推送."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.delivery import DeliveryHistory

TZ = ZoneInfo("Asia/Shanghai")


@dataclass
class IncrementalItem:
    announcement_id: str
    content_hash: str
    is_new: bool
    is_update: bool
    previous_hash: str | None = None


@dataclass
class IncrementalPlan:
    items: list[IncrementalItem]
    new_count: int
    update_count: int
    skipped_count: int


async def plan_incremental_delivery(
    db: AsyncSession,
    *,
    task_id: str,
    announcements: list[tuple[str, str]],
) -> IncrementalPlan:
    """根据任务历史决定本次应输出的公告.

    announcements: [(announcement_id, content_hash), ...]
    """
    if not announcements:
        return IncrementalPlan(items=[], new_count=0, update_count=0, skipped_count=0)

    ids = [a[0] for a in announcements]
    rows = (
        await db.execute(
            select(DeliveryHistory).where(
                DeliveryHistory.task_id == task_id,
                DeliveryHistory.announcement_id.in_(ids),
            )
        )
    ).scalars().all()
    by_ann = {r.announcement_id: r for r in rows}

    items: list[IncrementalItem] = []
    new_count = update_count = skipped = 0
    for ann_id, chash in announcements:
        prev = by_ann.get(ann_id)
        if prev is None:
            items.append(
                IncrementalItem(
                    announcement_id=ann_id,
                    content_hash=chash,
                    is_new=True,
                    is_update=False,
                )
            )
            new_count += 1
        elif prev.content_hash != chash:
            items.append(
                IncrementalItem(
                    announcement_id=ann_id,
                    content_hash=chash,
                    is_new=False,
                    is_update=True,
                    previous_hash=prev.content_hash,
                )
            )
            update_count += 1
        else:
            skipped += 1

    return IncrementalPlan(
        items=items,
        new_count=new_count,
        update_count=update_count,
        skipped_count=skipped,
    )


async def mark_delivered(
    db: AsyncSession,
    *,
    task_id: str,
    items: list[IncrementalItem],
    report_id: str | None,
    now: datetime | None = None,
) -> int:
    """仅在执行成功写入输出后调用；失败路径不得调用.

    report_id 可先用 execution_id，阶段六再关联 Word。
    """
    if not items:
        return 0
    ts = now or datetime.now(TZ)
    ann_ids = [i.announcement_id for i in items]
    existing = (
        await db.execute(
            select(DeliveryHistory).where(
                DeliveryHistory.task_id == task_id,
                DeliveryHistory.announcement_id.in_(ann_ids),
            )
        )
    ).scalars().all()
    by_ann = {r.announcement_id: r for r in existing}
    written = 0
    for item in items:
        row = by_ann.get(item.announcement_id)
        if row is None:
            db.add(
                DeliveryHistory(
                    task_id=task_id,
                    announcement_id=item.announcement_id,
                    content_hash=item.content_hash,
                    first_delivered_at=ts,
                    last_delivered_at=ts,
                    report_id=report_id,
                )
            )
            written += 1
        else:
            row.content_hash = item.content_hash
            row.last_delivered_at = ts
            row.report_id = report_id or row.report_id
            written += 1
    await db.flush()
    return written
