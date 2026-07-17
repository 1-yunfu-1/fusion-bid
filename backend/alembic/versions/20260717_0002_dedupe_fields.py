"""add dedupe merge fields on announcements

Revision ID: 20260717_0002
Revises: 20260717_0001
Create Date: 2026-07-17
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260717_0002"
down_revision: Union[str, None] = "20260717_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("tender_announcements") as batch:
        batch.add_column(
            sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.true())
        )
        batch.add_column(sa.Column("related_urls", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("related_sources", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("dedupe_reasons", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("project_code", sa.String(length=128), nullable=True))
    op.create_index(
        "ix_tender_announcements_project_code", "tender_announcements", ["project_code"]
    )


def downgrade() -> None:
    op.drop_index("ix_tender_announcements_project_code", table_name="tender_announcements")
    with op.batch_alter_table("tender_announcements") as batch:
        batch.drop_column("project_code")
        batch.drop_column("dedupe_reasons")
        batch.drop_column("related_sources")
        batch.drop_column("related_urls")
        batch.drop_column("is_primary")
