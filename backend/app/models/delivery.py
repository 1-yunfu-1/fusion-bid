"""DeliveryHistory — 增量交付历史."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DeliveryHistory(Base):
    __tablename__ = "delivery_histories"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("search_tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    announcement_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tender_announcements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    first_delivered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_delivered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    report_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
