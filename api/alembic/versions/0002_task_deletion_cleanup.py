"""Persist task deletion intent and retryable cleanup state.

Revision ID: 0002_task_deletion_cleanup
Revises: 0001_initial
Create Date: 2026-06-12
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0002_task_deletion_cleanup"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.add_column(
            sa.Column(
                "delete_requested_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "cleanup_pending",
                sa.Boolean(),
                server_default=sa.false(),
                nullable=False,
            )
        )
        batch_op.add_column(sa.Column("cleanup_error", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("cleanup_plan", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.drop_column("cleanup_plan")
        batch_op.drop_column("cleanup_error")
        batch_op.drop_column("cleanup_pending")
        batch_op.drop_column("delete_requested_at")
