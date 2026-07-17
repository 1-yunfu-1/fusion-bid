"""initial schema

Revision ID: 20260717_0001
Revises:
Create Date: 2026-07-17

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260717_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "search_tasks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("original_query", sa.Text(), nullable=False),
        sa.Column("parsed_intent", sa.JSON(), nullable=True),
        sa.Column("keywords", sa.JSON(), nullable=True),
        sa.Column("regions", sa.JSON(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("execute_immediately", sa.Boolean(), nullable=False),
        sa.Column("schedule_enabled", sa.Boolean(), nullable=False),
        sa.Column("schedule_type", sa.String(length=32), nullable=True),
        sa.Column("execute_time", sa.String(length=16), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "tender_announcements",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("source_name", sa.String(length=128), nullable=False),
        sa.Column("source_url", sa.String(length=2048), nullable=False),
        sa.Column("source_item_id", sa.String(length=256), nullable=True),
        sa.Column("requires_login", sa.Boolean(), nullable=False),
        sa.Column("data_mode", sa.String(length=16), nullable=False),
        sa.Column("publish_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("region", sa.String(length=128), nullable=True),
        sa.Column("province", sa.String(length=64), nullable=True),
        sa.Column("city", sa.String(length=64), nullable=True),
        sa.Column("keywords", sa.JSON(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("clean_content", sa.Text(), nullable=True),
        sa.Column("raw_content", sa.Text(), nullable=True),
        sa.Column("attachment_links", sa.JSON(), nullable=True),
        sa.Column("crawl_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("deduplication_key", sa.String(length=256), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tender_announcements_source_name", "tender_announcements", ["source_name"])
    op.create_index("ix_tender_announcements_source_item_id", "tender_announcements", ["source_item_id"])
    op.create_index("ix_tender_announcements_data_mode", "tender_announcements", ["data_mode"])
    op.create_index("ix_tender_announcements_content_hash", "tender_announcements", ["content_hash"])
    op.create_index(
        "ix_tender_announcements_deduplication_key", "tender_announcements", ["deduplication_key"]
    )

    op.create_table(
        "task_executions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("sources_requested", sa.JSON(), nullable=True),
        sa.Column("sources_succeeded", sa.JSON(), nullable=True),
        sa.Column("raw_result_count", sa.Integer(), nullable=False),
        sa.Column("filtered_result_count", sa.Integer(), nullable=False),
        sa.Column("duplicate_count", sa.Integer(), nullable=False),
        sa.Column("incremental_count", sa.Integer(), nullable=False),
        sa.Column("report_path", sa.String(length=1024), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["search_tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_task_executions_task_id", "task_executions", ["task_id"])

    op.create_table(
        "delivery_histories",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("announcement_id", sa.String(length=36), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("first_delivered_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("last_delivered_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("report_id", sa.String(length=36), nullable=True),
        sa.ForeignKeyConstraint(["announcement_id"], ["tender_announcements.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["search_tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_delivery_histories_task_id", "delivery_histories", ["task_id"])
    op.create_index("ix_delivery_histories_announcement_id", "delivery_histories", ["announcement_id"])
    op.create_index("ix_delivery_histories_report_id", "delivery_histories", ["report_id"])


def downgrade() -> None:
    op.drop_index("ix_delivery_histories_report_id", table_name="delivery_histories")
    op.drop_index("ix_delivery_histories_announcement_id", table_name="delivery_histories")
    op.drop_index("ix_delivery_histories_task_id", table_name="delivery_histories")
    op.drop_table("delivery_histories")
    op.drop_index("ix_task_executions_task_id", table_name="task_executions")
    op.drop_table("task_executions")
    op.drop_index("ix_tender_announcements_deduplication_key", table_name="tender_announcements")
    op.drop_index("ix_tender_announcements_content_hash", table_name="tender_announcements")
    op.drop_index("ix_tender_announcements_data_mode", table_name="tender_announcements")
    op.drop_index("ix_tender_announcements_source_item_id", table_name="tender_announcements")
    op.drop_index("ix_tender_announcements_source_name", table_name="tender_announcements")
    op.drop_table("tender_announcements")
    op.drop_table("search_tasks")
