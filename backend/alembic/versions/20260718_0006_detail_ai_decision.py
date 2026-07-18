"""detail extraction v2, decision analysis and full snapshot audit fields

Revision ID: 20260718_0006
Revises: 20260718_0005
Create Date: 2026-07-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260718_0006"
down_revision = "20260718_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tender_announcements") as batch:
        batch.add_column(sa.Column("detail_url", sa.String(length=2048), nullable=True))
        batch.add_column(sa.Column("content_format", sa.String(length=32), nullable=True))
        batch.add_column(
            sa.Column(
                "extraction_version",
                sa.String(length=16),
                nullable=False,
                server_default="v1",
            )
        )
        batch.add_column(sa.Column("announcement_type", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("analysis_data", sa.JSON(), nullable=True))
        batch.create_index(
            "ix_tender_announcements_extraction_version", ["extraction_version"]
        )
        batch.create_index(
            "ix_tender_announcements_announcement_type", ["announcement_type"]
        )
    op.execute(
        "UPDATE tender_announcements SET detail_url = "
        "'https://ctbpsp.com/#/bulletinDetail?uuid=' || source_item_id || "
        "'&inpvalue=&dataSource=0&tenderAgency=' "
        "WHERE source_name = 'cebpub' AND source_item_id IS NOT NULL "
        "AND length(source_item_id) = 32"
    )

    with op.batch_alter_table("task_executions") as batch:
        batch.add_column(
            sa.Column(
                "report_mode",
                sa.String(length=20),
                nullable=False,
                server_default="incremental",
            )
        )
        batch.add_column(
            sa.Column("deduplicate", sa.Boolean(), nullable=False, server_default=sa.true())
        )
        batch.add_column(
            sa.Column("truncated", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(
            sa.Column("detail_full_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column("detail_metadata_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column("detail_failed_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column(
                "detail_human_verification_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
    op.execute(
        "UPDATE task_executions SET report_mode = "
        "CASE WHEN report_scope = 'snapshot' THEN 'full_snapshot' ELSE 'incremental' END"
    )

    op.create_table(
        "company_profiles",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("profile_data", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "announcement_field_corrections",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("announcement_id", sa.String(length=36), nullable=False),
        sa.Column("field_name", sa.String(length=128), nullable=False),
        sa.Column("previous_value", sa.JSON(), nullable=True),
        sa.Column("corrected_value", sa.JSON(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "corrected_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["announcement_id"], ["tender_announcements.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_announcement_field_corrections_announcement_id",
        "announcement_field_corrections",
        ["announcement_id"],
    )
    op.create_index(
        "ix_announcement_field_corrections_field_name",
        "announcement_field_corrections",
        ["field_name"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_announcement_field_corrections_field_name",
        table_name="announcement_field_corrections",
    )
    op.drop_index(
        "ix_announcement_field_corrections_announcement_id",
        table_name="announcement_field_corrections",
    )
    op.drop_table("announcement_field_corrections")
    op.drop_table("company_profiles")
    with op.batch_alter_table("task_executions") as batch:
        batch.drop_column("detail_human_verification_count")
        batch.drop_column("detail_failed_count")
        batch.drop_column("detail_metadata_count")
        batch.drop_column("detail_full_count")
        batch.drop_column("truncated")
        batch.drop_column("deduplicate")
        batch.drop_column("report_mode")
    with op.batch_alter_table("tender_announcements") as batch:
        batch.drop_index("ix_tender_announcements_announcement_type")
        batch.drop_index("ix_tender_announcements_extraction_version")
        batch.drop_column("analysis_data")
        batch.drop_column("announcement_type")
        batch.drop_column("extraction_version")
        batch.drop_column("content_format")
        batch.drop_column("detail_url")
