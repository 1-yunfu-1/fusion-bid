"""lifecycle semantics, extraction cache and crawl quality audit

Revision ID: 20260719_0008
Revises: 20260719_0007
Create Date: 2026-07-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260719_0008"
down_revision = "20260719_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tender_announcements") as batch:
        batch.add_column(sa.Column("lifecycle_stage", sa.String(32), nullable=True))
        batch.add_column(sa.Column("procurement_method", sa.String(64), nullable=True))
        batch.add_column(sa.Column("document_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("extraction_fingerprint", sa.String(64), nullable=True))
        batch.create_index("ix_tender_announcements_lifecycle_stage", ["lifecycle_stage"])
        batch.create_index("ix_tender_announcements_procurement_method", ["procurement_method"])
        batch.create_index("ix_tender_announcements_document_hash", ["document_hash"])
        batch.create_index(
            "ix_tender_announcements_extraction_fingerprint",
            ["extraction_fingerprint"],
        )

    with op.batch_alter_table("task_executions") as batch:
        batch.add_column(sa.Column("detail_cap", sa.Integer(), server_default="30", nullable=False))
        batch.add_column(sa.Column("detail_cap_skipped", sa.Integer(), server_default="0", nullable=False))
        batch.add_column(sa.Column("coverage_status", sa.String(24), server_default="complete", nullable=False))
        batch.add_column(sa.Column("search_depth", sa.String(16), server_default="standard", nullable=False))
        batch.add_column(sa.Column("extraction_cache_hit_count", sa.Integer(), server_default="0", nullable=False))
        batch.add_column(sa.Column("llm_call_count", sa.Integer(), server_default="0", nullable=False))
        batch.add_column(sa.Column("llm_timeout_count", sa.Integer(), server_default="0", nullable=False))
        batch.add_column(sa.Column("opportunity_count", sa.Integer(), server_default="0", nullable=False))
        batch.add_column(sa.Column("lifecycle_count", sa.Integer(), server_default="0", nullable=False))
        batch.create_index("ix_task_executions_coverage_status", ["coverage_status"])
        batch.create_index("ix_task_executions_search_depth", ["search_depth"])

    op.create_table(
        "announcement_crawl_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "execution_id",
            sa.String(36),
            sa.ForeignKey("task_executions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "announcement_id",
            sa.String(36),
            sa.ForeignKey("tender_announcements.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source_name", sa.String(128), nullable=False),
        sa.Column("source_item_id", sa.String(256), nullable=True),
        sa.Column("stage", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(64), nullable=False),
        sa.Column("failure_code", sa.String(128), nullable=True),
        sa.Column("duration_ms", sa.Integer(), server_default="0", nullable=False),
        sa.Column("diagnostics", sa.JSON(), nullable=True),
        sa.Column("attempted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    for name in (
        "execution_id",
        "announcement_id",
        "source_name",
        "stage",
        "outcome",
        "failure_code",
        "attempted_at",
    ):
        op.create_index(f"ix_announcement_crawl_attempts_{name}", "announcement_crawl_attempts", [name])

    op.create_table(
        "announcement_quality_feedback",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "announcement_id",
            sa.String(36),
            sa.ForeignKey("tender_announcements.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("field_name", sa.String(128), nullable=True),
        sa.Column("verdict", sa.String(16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("snapshot", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    for name in ("announcement_id", "field_name", "verdict", "created_at"):
        op.create_index(f"ix_announcement_quality_feedback_{name}", "announcement_quality_feedback", [name])

    # 标题是生命周期的最高优先信号；无法确定的历史记录标记待复核。
    op.execute(
        sa.text(
            """
            UPDATE tender_announcements
            SET lifecycle_stage = CASE
              WHEN title LIKE '%终止%' OR title LIKE '%废标%' OR title LIKE '%流标%' OR title LIKE '%采购失败%' THEN '终止/废标'
              WHEN title LIKE '%更正%' OR title LIKE '%澄清%' OR title LIKE '%变更%' OR title LIKE '%补充公告%' THEN '更正/澄清'
              WHEN title LIKE '%中标%' OR title LIKE '%成交%' OR title LIKE '%结果公告%' OR title LIKE '%结果公示%' THEN '结果公告'
              WHEN title LIKE '%招标公告%' OR title LIKE '%采购公告%' OR title LIKE '%询价公告%' OR title LIKE '%磋商公告%' OR title LIKE '%谈判公告%' OR title LIKE '%资格预审公告%' THEN '机会公告'
              ELSE '待复核'
            END
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE tender_announcements
            SET procurement_method = CASE
              WHEN announcement_type IN ('公开招标','邀请招标','竞争性磋商','竞争性谈判','询价','单一来源','框架协议','电子竞价') THEN announcement_type
              WHEN title LIKE '%公开招标%' THEN '公开招标'
              WHEN title LIKE '%竞争性磋商%' THEN '竞争性磋商'
              WHEN title LIKE '%竞争性谈判%' THEN '竞争性谈判'
              WHEN title LIKE '%单一来源%' THEN '单一来源'
              WHEN title LIKE '%询价%' OR title LIKE '%询比%' THEN '询价'
              ELSE NULL
            END
            """
        )
    )


def downgrade() -> None:
    op.drop_table("announcement_quality_feedback")
    op.drop_table("announcement_crawl_attempts")
    with op.batch_alter_table("task_executions") as batch:
        batch.drop_index("ix_task_executions_search_depth")
        batch.drop_index("ix_task_executions_coverage_status")
        for name in (
            "lifecycle_count",
            "opportunity_count",
            "llm_timeout_count",
            "llm_call_count",
            "extraction_cache_hit_count",
            "search_depth",
            "coverage_status",
            "detail_cap_skipped",
            "detail_cap",
        ):
            batch.drop_column(name)
    with op.batch_alter_table("tender_announcements") as batch:
        batch.drop_index("ix_tender_announcements_extraction_fingerprint")
        batch.drop_index("ix_tender_announcements_document_hash")
        batch.drop_index("ix_tender_announcements_procurement_method")
        batch.drop_index("ix_tender_announcements_lifecycle_stage")
        batch.drop_column("extraction_fingerprint")
        batch.drop_column("document_hash")
        batch.drop_column("procurement_method")
        batch.drop_column("lifecycle_stage")
