"""Create upload and task persistence tables.

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-11
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enum(name: str, *values: str) -> sa.Enum:
    return sa.Enum(
        *values,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
    )


def upgrade() -> None:
    op.create_table(
        "uploads",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("client_id_hash", sa.String(), nullable=False),
        sa.Column("ip_hash", sa.String(), nullable=False),
        sa.Column("original_file_name", sa.String(), nullable=False),
        sa.Column("stored_path", sa.String(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            _enum(
                "uploadstatus_enum",
                "uploading",
                "validating",
                "ready",
                "invalid",
            ),
            nullable=False,
        ),
        sa.Column("validation_result", sa.JSON(), nullable=True),
        sa.Column("selected_hdu", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_uploads_status_expires_at",
        "uploads",
        ["status", "expires_at"],
        unique=False,
    )

    op.create_table(
        "tasks",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column(
            "type",
            _enum("tasktype_enum", "analysis", "processing"),
            nullable=False,
        ),
        sa.Column(
            "status",
            _enum(
                "taskstatus_enum",
                "queued",
                "running",
                "cancelling",
                "cancelled",
                "completed",
                "failed",
                "expired",
            ),
            nullable=False,
        ),
        sa.Column("stage", sa.String(), nullable=True),
        sa.Column("progress", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("client_id_hash", sa.String(), nullable=False),
        sa.Column("ip_hash", sa.String(), nullable=False),
        sa.Column("upload_id", sa.String(), nullable=True),
        sa.Column("source_task_id", sa.String(), nullable=True),
        sa.Column(
            "style",
            _enum(
                "processingstyle_enum",
                "realistic",
                "balanced",
                "artistic",
            ),
            nullable=True,
        ),
        sa.Column("selected_hdu", sa.Integer(), nullable=True),
        sa.Column("input_path", sa.String(), nullable=True),
        sa.Column("result_manifest", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column("retryable", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("quota_charged", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("free_retry_used", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["source_task_id"], ["tasks.id"]),
        sa.ForeignKeyConstraint(["upload_id"], ["uploads.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_tasks_status_created_at",
        "tasks",
        ["status", "created_at"],
        unique=False,
    )
    op.create_index("ix_tasks_expires_at", "tasks", ["expires_at"], unique=False)

    op.create_table(
        "task_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.String(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column(
            "level",
            _enum("eventlevel_enum", "debug", "info", "warning", "error"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("payload", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "sequence", name="uq_task_event_sequence"),
    )
    op.create_table(
        "daily_usage",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("client_id_hash", sa.String(), nullable=False),
        sa.Column("ip_hash", sa.String(), nullable=False),
        sa.Column("count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "date",
            "client_id_hash",
            "ip_hash",
            name="uq_daily_usage_client",
        ),
    )


def downgrade() -> None:
    op.drop_table("daily_usage")
    op.drop_table("task_events")
    op.drop_index("ix_tasks_expires_at", table_name="tasks")
    op.drop_index("ix_tasks_status_created_at", table_name="tasks")
    op.drop_table("tasks")
    op.drop_index("ix_uploads_status_expires_at", table_name="uploads")
    op.drop_table("uploads")
