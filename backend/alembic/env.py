"""Alembic 环境：从应用配置读取 DATABASE_URL，支持异步 URL 转为同步迁移."""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

import sqlalchemy as sa
from alembic import context
from sqlalchemy import inspect, pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.config import get_settings
from app.core.database import Base
from app import models  # noqa: F401  # 注册 metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


_LEGACY_CORE_TABLES = {
    "search_tasks",
    "tender_announcements",
    "task_executions",
    "delivery_histories",
}


def _infer_legacy_revision(connection: Connection) -> str | None:
    """Infer the last completed revision of an unversioned legacy database."""

    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    if not _LEGACY_CORE_TABLES.issubset(tables):
        return None

    def columns(table_name: str) -> set[str]:
        return {column["name"] for column in inspector.get_columns(table_name)}

    task_columns = columns("search_tasks")
    announcement_columns = columns("tender_announcements")
    execution_columns = columns("task_executions")

    if (
        {"company_profiles", "announcement_field_corrections"}.issubset(tables)
        and {"detail_url", "extraction_version", "analysis_data"}.issubset(
            announcement_columns
        )
        and {"report_mode", "detail_full_count"}.issubset(execution_columns)
    ):
        return "20260718_0006"
    if (
        {"detail_status", "source_metadata", "extraction_data"}.issubset(
            announcement_columns
        )
        and {"report_scope", "analysis_data"}.issubset(execution_columns)
    ):
        return "20260718_0005"
    if "trigger_type" in execution_columns:
        return "20260717_0004"
    if {"execute_date", "is_paused", "next_run_at"}.issubset(task_columns):
        return "20260717_0003"
    if {"is_primary", "project_code", "related_sources"}.issubset(
        announcement_columns
    ):
        return "20260717_0002"
    return "20260717_0001"


def _adopt_unversioned_legacy_database(connection: Connection) -> None:
    """Create only a baseline marker; application rows are never rewritten."""

    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    if "alembic_version" in tables:
        current = connection.execute(sa.text("SELECT version_num FROM alembic_version"))
        if current.first() is not None:
            return

    revision = _infer_legacy_revision(connection)
    if revision is None:
        return

    version_table = sa.Table(
        "alembic_version",
        sa.MetaData(),
        sa.Column("version_num", sa.String(length=32), nullable=False),
    )
    version_table.create(connection, checkfirst=True)
    connection.execute(version_table.delete())
    connection.execute(version_table.insert().values(version_num=revision))
    # SQLAlchemy 2 starts a transaction for the baseline INSERT.  Finish that
    # transaction before Alembic opens its own migration transaction; otherwise
    # closing the async connection would roll the adopted revision back.
    connection.commit()


def get_url() -> str:
    return get_settings().database_url


def run_migrations_offline() -> None:
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    if connection.dialect.name == "sqlite":
        _adopt_unversioned_legacy_database(connection)
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()
    # SQLite DDL 会立即生效，但通过异步连接运行时，alembic_version 的 UPDATE
    # 仍可能留在隐式事务中并在连接关闭时回滚。显式提交保证结构与版本标识一致。
    if connection.in_transaction():
        connection.commit()


async def run_async_migrations() -> None:
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = get_url()
    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
