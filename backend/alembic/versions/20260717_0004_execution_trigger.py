"""add trigger type on task executions

Revision ID: 20260717_0004
Revises: 20260717_0003
Create Date: 2026-07-17
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260717_0004"
down_revision: Union[str, None] = "20260717_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("task_executions") as batch:
        batch.add_column(
            sa.Column(
                "trigger_type",
                sa.String(length=16),
                nullable=False,
                server_default="manual",
            )
        )
        batch.create_index("ix_task_executions_trigger_type", ["trigger_type"])


def downgrade() -> None:
    with op.batch_alter_table("task_executions") as batch:
        batch.drop_index("ix_task_executions_trigger_type")
        batch.drop_column("trigger_type")
