"""Persist verified-detail quality and execution report-analysis metadata.

Revision ID: 20260718_0005
Revises: 20260717_0004
Create Date: 2026-07-18
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260718_0005"
down_revision = "20260717_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tender_announcements") as batch:
        batch.add_column(
            sa.Column(
                "detail_status", sa.String(length=24), nullable=False, server_default="unknown"
            )
        )
        batch.add_column(sa.Column("source_metadata", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("extraction_data", sa.JSON(), nullable=True))
        batch.create_index(
            "ix_tender_announcements_detail_status", ["detail_status"], unique=False
        )
    with op.batch_alter_table("task_executions") as batch:
        batch.add_column(
            sa.Column(
                "report_scope", sa.String(length=16), nullable=False, server_default="incremental"
            )
        )
        batch.add_column(sa.Column("analysis_data", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("task_executions") as batch:
        batch.drop_column("analysis_data")
        batch.drop_column("report_scope")
    with op.batch_alter_table("tender_announcements") as batch:
        batch.drop_index("ix_tender_announcements_detail_status")
        batch.drop_column("extraction_data")
        batch.drop_column("source_metadata")
        batch.drop_column("detail_status")
