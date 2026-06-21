"""Add review_required task status.

Revision ID: 0004_task_review_required
Revises: 0003_upload_cleanup_intent
Create Date: 2026-06-21
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0004_task_review_required"
down_revision: str | None = "0003_upload_cleanup_intent"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


OLD_STATUSES = (
    "queued",
    "running",
    "cancelling",
    "cancelled",
    "completed",
    "failed",
    "expired",
)
NEW_STATUSES = (*OLD_STATUSES[:5], "review_required", *OLD_STATUSES[5:])


def _status_enum(values: tuple[str, ...]) -> sa.Enum:
    return sa.Enum(
        *values,
        name="taskstatus_enum",
        native_enum=False,
        create_constraint=True,
    )


def upgrade() -> None:
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=_status_enum(OLD_STATUSES),
            type_=_status_enum(NEW_STATUSES),
            existing_nullable=False,
        )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE tasks SET status = 'failed', "
            "error_code = COALESCE(error_code, 'review_required') "
            "WHERE status = 'review_required'"
        )
    )
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=_status_enum(NEW_STATUSES),
            type_=_status_enum(OLD_STATUSES),
            existing_nullable=False,
        )
