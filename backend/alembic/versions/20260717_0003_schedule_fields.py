"""add schedule control fields on search_tasks

Revision ID: 20260717_0003
Revises: 20260717_0002
Create Date: 2026-07-17
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260717_0003"
down_revision: Union[str, None] = "20260717_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("search_tasks") as batch:
        batch.add_column(sa.Column("execute_date", sa.Date(), nullable=True))
        batch.add_column(
            sa.Column("is_paused", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("search_tasks") as batch:
        batch.drop_column("next_run_at")
        batch.drop_column("last_run_at")
        batch.drop_column("is_paused")
        batch.drop_column("execute_date")
