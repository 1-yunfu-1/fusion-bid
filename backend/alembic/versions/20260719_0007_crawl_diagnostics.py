"""persist bounded crawl pipeline diagnostics

Revision ID: 20260719_0007
Revises: 20260718_0006
Create Date: 2026-07-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260719_0007"
down_revision = "20260718_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("task_executions")}
    if "crawl_diagnostics" not in columns:
        with op.batch_alter_table("task_executions") as batch:
            batch.add_column(sa.Column("crawl_diagnostics", sa.JSON(), nullable=True))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("task_executions")}
    if "crawl_diagnostics" in columns:
        with op.batch_alter_table("task_executions") as batch:
            batch.drop_column("crawl_diagnostics")
