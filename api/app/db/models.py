from datetime import date as Date
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    false,
    text,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from app.db.base import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        return dialect.type_descriptor(DateTime(timezone=dialect.name != "sqlite"))

    def process_bind_param(
        self,
        value: datetime | None,
        dialect: Dialect,
    ) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("datetime values must be UTC-aware")

        normalized = value.astimezone(UTC)
        if dialect.name == "sqlite":
            return normalized.replace(tzinfo=None)
        return normalized

    def process_result_value(
        self,
        value: datetime | None,
        _dialect: Dialect,
    ) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


def enum_type(enum_class: type[StrEnum]) -> Enum:
    return Enum(
        enum_class,
        name=f"{enum_class.__name__.lower()}_enum",
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda members: [member.value for member in members],
    )


class UploadStatus(StrEnum):
    UPLOADING = "uploading"
    VALIDATING = "validating"
    READY = "ready"
    INVALID = "invalid"


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    REVIEW_REQUIRED = "review_required"
    FAILED = "failed"
    EXPIRED = "expired"


class TaskType(StrEnum):
    ANALYSIS = "analysis"
    PROCESSING = "processing"


class ProcessingStyle(StrEnum):
    REALISTIC = "realistic"
    BALANCED = "balanced"
    ARTISTIC = "artistic"


class EventLevel(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class Upload(Base):
    __tablename__ = "uploads"
    __table_args__ = (Index("ix_uploads_status_expires_at", "status", "expires_at"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    client_id_hash: Mapped[str] = mapped_column(String, nullable=False)
    ip_hash: Mapped[str] = mapped_column(String, nullable=False)
    original_file_name: Mapped[str] = mapped_column(String, nullable=False)
    stored_path: Mapped[str] = mapped_column(String, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[UploadStatus] = mapped_column(enum_type(UploadStatus), nullable=False)
    validation_result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    selected_hdu: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    claimed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    cleanup_pending: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=false(),
    )
    cleanup_error: Mapped[str | None] = mapped_column(String)
    cleanup_plan: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    tasks: Mapped[list["Task"]] = relationship(back_populates="upload")


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        Index("ix_tasks_status_created_at", "status", "created_at"),
        Index("ix_tasks_expires_at", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    type: Mapped[TaskType] = mapped_column(enum_type(TaskType), nullable=False)
    status: Mapped[TaskStatus] = mapped_column(enum_type(TaskStatus), nullable=False)
    stage: Mapped[str | None] = mapped_column(String)
    progress: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    client_id_hash: Mapped[str] = mapped_column(String, nullable=False)
    ip_hash: Mapped[str] = mapped_column(String, nullable=False)
    upload_id: Mapped[str | None] = mapped_column(ForeignKey("uploads.id"))
    source_task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"))
    style: Mapped[ProcessingStyle | None] = mapped_column(enum_type(ProcessingStyle))
    selected_hdu: Mapped[int | None] = mapped_column(Integer)
    input_path: Mapped[str | None] = mapped_column(String)
    result_manifest: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String)
    error_message: Mapped[str | None] = mapped_column(String)
    retryable: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    quota_charged: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=false(),
    )
    free_retry_used: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=false(),
    )
    cancel_requested_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    delete_requested_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    cleanup_pending: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=false(),
    )
    cleanup_error: Mapped[str | None] = mapped_column(String)
    cleanup_plan: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    upload: Mapped[Upload | None] = relationship(back_populates="tasks")
    source_task: Mapped["Task | None"] = relationship(
        back_populates="derived_tasks",
        remote_side="Task.id",
    )
    derived_tasks: Mapped[list["Task"]] = relationship(back_populates="source_task")
    events: Mapped[list["TaskEvent"]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
    )


class TaskEvent(Base):
    __tablename__ = "task_events"
    __table_args__ = (UniqueConstraint("task_id", "sequence", name="uq_task_event_sequence"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    level: Mapped[EventLevel] = mapped_column(enum_type(EventLevel), nullable=False)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        server_default=text("'{}'"),
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    task: Mapped[Task] = relationship(back_populates="events")


class DailyUsage(Base):
    __tablename__ = "daily_usage"
    __table_args__ = (
        UniqueConstraint(
            "date",
            "client_id_hash",
            "ip_hash",
            name="uq_daily_usage_client",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[Date] = mapped_column(nullable=False)
    client_id_hash: Mapped[str] = mapped_column(String, nullable=False)
    ip_hash: Mapped[str] = mapped_column(String, nullable=False)
    count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
