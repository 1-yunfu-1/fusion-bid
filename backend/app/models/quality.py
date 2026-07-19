"""追加式采集审计与人工质量反馈。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.core.database import Base


class AnnouncementCrawlAttempt(Base):
    __tablename__ = "announcement_crawl_attempts"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    execution_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("task_executions.id", ondelete="CASCADE"), index=True
    )
    announcement_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("tender_announcements.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    source_item_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    stage: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    outcome: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    failure_code: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    diagnostics: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class AnnouncementQualityFeedback(Base):
    __tablename__ = "announcement_quality_feedback"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    announcement_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tender_announcements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    field_name: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    verdict: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
