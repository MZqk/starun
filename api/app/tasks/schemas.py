from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, model_validator

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


class UsageResponse(BaseModel):
    date: date
    limit: int
    used: int
    remaining: int
