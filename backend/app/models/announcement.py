"""TenderAnnouncement — 招投标公告."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.core.database import Base


class TenderAnnouncement(Base):
    __tablename__ = "tender_announcements"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    source_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    source_item_id: Mapped[str | None] = mapped_column(String(256), nullable=True, index=True)
    requires_login: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    data_mode: Mapped[str] = mapped_column(String(16), default="live", nullable=False, index=True)
    publish_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    region: Mapped[str | None] = mapped_column(String(128), nullable=True)
    province: Mapped[str | None] = mapped_column(String(64), nullable=True)
    city: Mapped[str | None] = mapped_column(String(64), nullable=True)
    keywords: Mapped[list | None] = mapped_column(JSON, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    clean_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    attachment_links: Mapped[list | None] = mapped_column(JSON, nullable=True)
    crawl_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    deduplication_key: Mapped[str | None] = mapped_column(String(256), nullable=True, index=True)
    # 去重合并元数据（主记录保留；被合并来源不无记录删除，信息写入 JSON）
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    related_urls: Mapped[list | None] = mapped_column(JSON, nullable=True)
    related_sources: Mapped[list | None] = mapped_column(JSON, nullable=True)
    dedupe_reasons: Mapped[list | None] = mapped_column(JSON, nullable=True)
    project_code: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
