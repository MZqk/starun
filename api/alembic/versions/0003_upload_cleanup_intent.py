"""Persist retryable upload cleanup intent.

Revision ID: 0003_upload_cleanup_intent
Revises: 0002_task_deletion_cleanup
Create Date: 2026-06-15
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0003_upload_cleanup_intent"
down_revision: str | None = "0002_task_deletion_cleanup"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("uploads") as batch_op:
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
    with op.batch_alter_table("uploads") as batch_op:
        batch_op.drop_column("cleanup_plan")
        batch_op.drop_column("cleanup_error")
        batch_op.drop_column("cleanup_pending")
