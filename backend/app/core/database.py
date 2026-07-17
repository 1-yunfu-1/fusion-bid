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


async def init_db() -> None:
    """创建全部表（开发便捷；正式迁移用 Alembic）."""
    # 延迟导入模型以注册 metadata
    from app import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if engine.dialect.name == "sqlite":
            await conn.run_sync(_sqlite_add_missing_columns)


async def check_db() -> bool:
    """连通性探测."""
    from sqlalchemy import text

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
