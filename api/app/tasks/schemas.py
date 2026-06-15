from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.db.models import ProcessingStyle, TaskStatus, TaskType


class AnalysisTaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    upload_id: str


class ProcessingTaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    upload_id: str | None = None
    source_task_id: str | None = None
    style: ProcessingStyle = ProcessingStyle.BALANCED

    @model_validator(mode="after")
    def validate_exactly_one_source(self) -> "ProcessingTaskCreate":
        if (self.upload_id is None) == (self.source_task_id is None):
            raise ValueError("exactly one of upload_id or source_task_id is required")
        return self


class TaskResponse(BaseModel):
    task_id: str
    type: TaskType
    status: TaskStatus
    quota_charged: bool
    created_at: datetime
    expires_at: datetime | None
    style: ProcessingStyle | None


class TaskErrorResponse(BaseModel):
    error_code: str
    message: str
    retryable: bool = False
    quota_charged: bool = False
    diagnostic_id: str | None = None


class TaskResultResponse(BaseModel):
    manifest_available: bool
    summary: dict[str, Any] | None = None
    artifacts: list[str] = Field(default_factory=list)


class TaskDetailResponse(BaseModel):
    id: str
    type: TaskType
    status: TaskStatus
    stage: str | None
    progress: int
    style: ProcessingStyle | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    expires_at: datetime | None
    error_code: str | None
    message: str | None
    retryable: bool
    quota_charged: bool
    cleanup_pending: bool
    result: TaskResultResponse
    selected_hdu: int | None
    inspection: dict[str, Any] | None = None


class TaskEventResponse(BaseModel):
    sequence: int
    level: str
    event_type: str
    payload: dict[str, Any]
    created_at: datetime


class TaskEventsResponse(BaseModel):
    events: list[TaskEventResponse]
    next_after: int
    has_more: bool


class UsageResponse(BaseModel):
    date: date
    limit: int
    used: int
    remaining: int
