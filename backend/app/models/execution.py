"""TaskExecution — 单次任务执行记录."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.core.database import Base


class TaskExecution(Base):
    __tablename__ = "task_executions"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("search_tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    trigger_type: Mapped[str] = mapped_column(
        String(16), default="manual", nullable=False, index=True
    )
    report_scope: Mapped[str] = mapped_column(
        String(16), default="incremental", nullable=False
    )  # incremental | snapshot
    report_mode: Mapped[str] = mapped_column(
        String(20), default="incremental", nullable=False
    )  # incremental | full_snapshot
    deduplicate: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    truncated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    detail_full_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    detail_metadata_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    detail_failed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    detail_human_verification_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    detail_cap: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    detail_cap_skipped: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    coverage_status: Mapped[str] = mapped_column(
        String(24), default="complete", nullable=False, index=True
    )
    search_depth: Mapped[str] = mapped_column(
        String(16), default="standard", nullable=False, index=True
    )
    extraction_cache_hit_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    llm_call_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    llm_timeout_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    opportunity_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lifecycle_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sources_requested: Mapped[list | None] = mapped_column(JSON, nullable=True)
    sources_succeeded: Mapped[list | None] = mapped_column(JSON, nullable=True)
    raw_result_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    filtered_result_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    incremental_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    report_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # 规则分析始终可用；LLM 仅在通过证据校验后补充，不保存任何密钥或原始提示词。
    analysis_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # 只保存分阶段计数、耗时与公开失败代码；不保存浏览器端口、本机路径或站点状态。
    crawl_diagnostics: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    task = relationship("SearchTask", back_populates="executions")
