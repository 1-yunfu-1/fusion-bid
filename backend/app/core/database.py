"""数据库引擎与会话（SQLAlchemy 2 异步）."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings


class Base(DeclarativeBase):
    """ORM 声明基类."""


def _make_engine():
    settings = get_settings()
    connect_args = {}
    if settings.database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    return create_async_engine(
        settings.database_url,
        echo=settings.app_debug and settings.app_env == "development",
        connect_args=connect_args,
    )


engine = _make_engine()
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session


def _sqlite_add_missing_columns(sync_conn) -> None:
    """开发库增量补齐新列（create_all 不会 ALTER 已有表）."""
    from sqlalchemy import inspect, text

    insp = inspect(sync_conn)
    if "tender_announcements" not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_columns("tender_announcements")}
    alters: list[str] = []
    if "is_primary" not in existing:
        alters.append("ALTER TABLE tender_announcements ADD COLUMN is_primary BOOLEAN DEFAULT 1")
    if "related_urls" not in existing:
        alters.append("ALTER TABLE tender_announcements ADD COLUMN related_urls JSON")
    if "related_sources" not in existing:
        alters.append("ALTER TABLE tender_announcements ADD COLUMN related_sources JSON")
    if "dedupe_reasons" not in existing:
        alters.append("ALTER TABLE tender_announcements ADD COLUMN dedupe_reasons JSON")
    if "project_code" not in existing:
        alters.append("ALTER TABLE tender_announcements ADD COLUMN project_code VARCHAR(128)")
    if "detail_status" not in existing:
        alters.append(
            "ALTER TABLE tender_announcements ADD COLUMN "
            "detail_status VARCHAR(24) DEFAULT 'unknown' NOT NULL"
        )
    if "source_metadata" not in existing:
        alters.append("ALTER TABLE tender_announcements ADD COLUMN source_metadata JSON")
    if "extraction_data" not in existing:
        alters.append("ALTER TABLE tender_announcements ADD COLUMN extraction_data JSON")
    if "detail_url" not in existing:
        alters.append("ALTER TABLE tender_announcements ADD COLUMN detail_url VARCHAR(2048)")
    if "content_format" not in existing:
        alters.append("ALTER TABLE tender_announcements ADD COLUMN content_format VARCHAR(32)")
    if "extraction_version" not in existing:
        alters.append(
            "ALTER TABLE tender_announcements ADD COLUMN "
            "extraction_version VARCHAR(16) DEFAULT 'v1' NOT NULL"
        )
    if "announcement_type" not in existing:
        alters.append("ALTER TABLE tender_announcements ADD COLUMN announcement_type VARCHAR(64)")
    if "analysis_data" not in existing:
        alters.append("ALTER TABLE tender_announcements ADD COLUMN analysis_data JSON")
    for stmt in alters:
        sync_conn.execute(text(stmt))

    # search_tasks 调度字段
    if "search_tasks" in insp.get_table_names():
        tcols = {c["name"] for c in insp.get_columns("search_tasks")}
        task_alters: list[str] = []
        if "execute_date" not in tcols:
            task_alters.append("ALTER TABLE search_tasks ADD COLUMN execute_date DATE")
        if "is_paused" not in tcols:
            task_alters.append(
                "ALTER TABLE search_tasks ADD COLUMN is_paused BOOLEAN DEFAULT 0 NOT NULL"
            )
        if "last_run_at" not in tcols:
            task_alters.append("ALTER TABLE search_tasks ADD COLUMN last_run_at DATETIME")
        if "next_run_at" not in tcols:
            task_alters.append("ALTER TABLE search_tasks ADD COLUMN next_run_at DATETIME")
        for stmt in task_alters:
            sync_conn.execute(text(stmt))

    # task_executions 执行触发类型
    if "task_executions" in insp.get_table_names():
        ecols = {c["name"] for c in insp.get_columns("task_executions")}
        exec_alters: list[str] = []
        if "trigger_type" not in ecols:
            exec_alters.append(
                "ALTER TABLE task_executions ADD COLUMN "
                "trigger_type VARCHAR(16) DEFAULT 'manual' NOT NULL"
            )
        if "report_scope" not in ecols:
            exec_alters.append(
                "ALTER TABLE task_executions ADD COLUMN "
                "report_scope VARCHAR(16) DEFAULT 'incremental' NOT NULL"
            )
        if "analysis_data" not in ecols:
            exec_alters.append("ALTER TABLE task_executions ADD COLUMN analysis_data JSON")
        if "crawl_diagnostics" not in ecols:
            exec_alters.append(
                "ALTER TABLE task_executions ADD COLUMN crawl_diagnostics JSON"
            )
        if "report_mode" not in ecols:
            exec_alters.append(
                "ALTER TABLE task_executions ADD COLUMN "
                "report_mode VARCHAR(20) DEFAULT 'incremental' NOT NULL"
            )
        if "deduplicate" not in ecols:
            exec_alters.append(
                "ALTER TABLE task_executions ADD COLUMN deduplicate BOOLEAN DEFAULT 1 NOT NULL"
            )
        if "truncated" not in ecols:
            exec_alters.append(
                "ALTER TABLE task_executions ADD COLUMN truncated BOOLEAN DEFAULT 0 NOT NULL"
            )
        for name in (
            "detail_full_count",
            "detail_metadata_count",
            "detail_failed_count",
            "detail_human_verification_count",
        ):
            if name not in ecols:
                exec_alters.append(
                    f"ALTER TABLE task_executions ADD COLUMN {name} INTEGER DEFAULT 0 NOT NULL"
                )
        for stmt in exec_alters:
            sync_conn.execute(
                text(stmt)
            )


async def init_db() -> None:
    """创建全部表（开发便捷；正式迁移用 Alembic）."""
    # 延迟导入模型以注册 metadata
    from app import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if engine.dialect.name == "sqlite":
            await conn.run_sync(_sqlite_add_missing_columns)
    await _upgrade_legacy_extractions()


async def _upgrade_legacy_extractions() -> None:
    """已有正文的 v1 记录自动重抽取；只有列表元数据的标记待重采。"""
    from sqlalchemy import or_, select

    from app.models.announcement import TenderAnnouncement
    from app.reports.fields import build_extraction_data
    from app.sources.cebpub_source import current_detail_url

    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(TenderAnnouncement).where(
                    or_(
                        TenderAnnouncement.extraction_version != "v2",
                        TenderAnnouncement.extraction_data.is_(None),
                        TenderAnnouncement.detail_url.is_(None),
                    )
                )
            )
        ).scalars().all()
        changed = False
        for row in rows:
            if row.source_name == "cebpub" and row.source_item_id:
                mapped_detail_url = current_detail_url(row.source_item_id)
                if row.detail_url != mapped_detail_url:
                    row.detail_url = mapped_detail_url
                    changed = True
            if row.detail_status == "full" and (row.clean_content or "").strip():
                row.extraction_data = build_extraction_data(
                    title=row.title,
                    clean_content=row.clean_content or "",
                    summary=row.summary or "",
                    region=row.region,
                    project_code=row.project_code,
                    publish_time=row.publish_time,
                    detail_status="full",
                    source_metadata=row.source_metadata or {},
                )
                row.extraction_version = "v2"
                fields = (row.extraction_data or {}).get("fields") or {}
                row.announcement_type = fields.get("announcement_type")
            else:
                row.extraction_version = "needs_recrawl"
                if row.detail_status in {"unknown", "failed"}:
                    row.detail_status = "metadata_only"
            changed = True
        if changed:
            await session.commit()


async def check_db() -> bool:
    """连通性探测."""
    from sqlalchemy import text

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def database_revision() -> str:
    """返回当前 Alembic 版本；开发库未建版本表时明确标注。"""
    from sqlalchemy import text

    try:
        async with engine.connect() as conn:
            value = await conn.scalar(text("SELECT version_num FROM alembic_version LIMIT 1"))
        return str(value or "unversioned")
    except Exception:  # noqa: BLE001
        return "unversioned"
