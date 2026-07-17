"""公告查询 API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.announcement import TenderAnnouncement

router = APIRouter(prefix="/announcements", tags=["announcements"])


@router.get("")
async def list_announcements(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    source_name: str | None = None,
    data_mode: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    stmt = select(TenderAnnouncement).order_by(TenderAnnouncement.created_at.desc())
    count_stmt = select(func.count()).select_from(TenderAnnouncement)
    if source_name:
        stmt = stmt.where(TenderAnnouncement.source_name == source_name)
        count_stmt = count_stmt.where(TenderAnnouncement.source_name == source_name)
    if data_mode:
        stmt = stmt.where(TenderAnnouncement.data_mode == data_mode)
        count_stmt = count_stmt.where(TenderAnnouncement.data_mode == data_mode)
    total = await db.scalar(count_stmt) or 0
    rows = (await db.execute(stmt.offset(offset).limit(limit))).scalars().all()
    items = []
    for a in rows:
        items.append(
            {
                "id": a.id,
                "title": a.title,
                "source_name": a.source_name,
                "source_url": a.source_url,
                "data_mode": a.data_mode,
                "requires_login": a.requires_login,
                "publish_time": a.publish_time.isoformat() if a.publish_time else None,
                "region": a.region,
                "keywords": a.keywords,
                "summary": a.summary,
                "attachment_links": a.attachment_links or [],
                "crawl_time": a.crawl_time.isoformat() if a.crawl_time else None,
                "is_primary": getattr(a, "is_primary", True),
                "related_urls": a.related_urls or [a.source_url],
                "related_sources": a.related_sources or [],
                "dedupe_reasons": a.dedupe_reasons or [],
                "project_code": getattr(a, "project_code", None),
            }
        )
    return {"items": items, "total": int(total)}


@router.get("/{announcement_id}")
async def get_announcement(announcement_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    a = await db.get(TenderAnnouncement, announcement_id)
    if not a:
        raise HTTPException(status_code=404, detail="公告不存在")
    return {
        "id": a.id,
        "title": a.title,
        "source_name": a.source_name,
        "source_url": a.source_url,
        "source_item_id": a.source_item_id,
        "data_mode": a.data_mode,
        "requires_login": a.requires_login,
        "publish_time": a.publish_time.isoformat() if a.publish_time else None,
        "region": a.region,
        "province": a.province,
        "city": a.city,
        "keywords": a.keywords,
        "summary": a.summary,
        "clean_content": a.clean_content,
        "attachment_links": a.attachment_links or [],
        "crawl_time": a.crawl_time.isoformat() if a.crawl_time else None,
        "content_hash": a.content_hash,
        "deduplication_key": a.deduplication_key,
    }
