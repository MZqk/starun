import re
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import Task, TaskEvent
from app.db.session import get_db_session
from app.security.rate_limit import rate_limit_response
from app.tasks.schemas import (
    AnalysisTaskCreate,
    ProcessingTaskCreate,
    TaskDetailResponse,
    TaskErrorResponse,
    TaskEventResponse,
    TaskEventsResponse,
    TaskResultResponse,
    TaskResponse,
    UsageResponse,
)
from app.tasks.service import TaskCreationError, TaskService
from app.uploads.errors import missing_client_id_error
from app.uploads.service import get_settings
from app.usage.service import get_daily_usage

router = APIRouter(tags=["tasks"])
PUBLIC_ERROR_MESSAGES = {
    "agent_guardrail": "Agent output was rejected.",
    "agent_not_configured": "Agent provider credentials are not configured.",
    "agent_provider_error": "Agent provider request failed.",
    "skill_execution_failed": "The selected skill failed to execute.",
    "skill_output_invalid": "The selected skill returned invalid output.",
    "restart_interrupted": "Task execution was interrupted by an application restart.",
    "resource_error": "The task could not acquire the required resources.",
    "resource_exhausted": "The task could not acquire the required resources.",
    "task_timeout": "The task exceeded its execution time limit.",
    "timeout": "The task exceeded its execution time limit.",
    "task_store": "The task store could not complete the operation.",
    "task_store_busy": "The task store is busy. Please retry shortly.",
    "task_store_unavailable": "The task store is temporarily unavailable.",
    "system_error": "The task failed because of an internal error.",
    "user_cancelled": "The task was cancelled.",
    "user_deleted": "The task was deleted.",
}
SENSITIVE_KEYS = {
    "cwd",
    "dir",
    "directory",
    "file",
    "filesystem",
    "folder",
    "path",
    "file_path",
    "input_path",
    "root",
    "stored_path",
    "traceback",
    "secret",
    "token",
    "authorization",
}
EVENT_KEY_ALLOWLISTS = {
    "restart_interrupted": {"error_code"},
    "task_cancelled": set(),
    "task_completed": {"progress"},
    "task_failed": {"diagnostic_id", "error_code", "message", "retryable"},
    "task_started": {"task_type"},
    "task_timeout": {"error_code"},
}
DETAILED_TASK_ERROR_CODES = {
    "agent_provider_error",
    "skill_execution_failed",
}
WINDOWS_DRIVE_PATH = re.compile(r"^[A-Za-z]:[\\/]")
TRAVERSAL_PATH = re.compile(r"(^|[\\/])\.\.([\\/]|$)")
_DROP = object()


def _request_ip(request: Request) -> str:
    return request.client.host if request.client is not None else ""


def _error_response(error: TaskCreationError) -> JSONResponse:
    body = TaskErrorResponse(
        error_code=error.error_code,
        message=error.message,
        retryable=error.retryable,
        quota_charged=False,
    )
    return JSONResponse(
        status_code=error.status_code,
        content=body.model_dump(mode="json", exclude_none=True),
    )


def _missing_client_response() -> JSONResponse:
    error = missing_client_id_error()
    return _error_response(
        TaskCreationError(
            error.status_code,
            error.error_code,
            error.message,
            retryable=error.retryable,
        )
    )


def _task_response(task: Task) -> TaskResponse:
    return TaskResponse(
        task_id=task.id,
        type=task.type,
        status=task.status,
        quota_charged=task.quota_charged,
        created_at=task.created_at,
        expires_at=task.expires_at,
        style=task.style,
    )


def _notify_task_executor(request: Request) -> None:
    executor = getattr(request.app.state, "task_executor", None)
    notify = getattr(executor, "notify", None)
    if callable(notify):
        notify()


def _task_detail(task: Task) -> TaskDetailResponse:
    manifest = task.result_manifest if isinstance(task.result_manifest, dict) else {}
    raw_artifacts = manifest.get("artifacts")
    artifact_names: list[str] = []
    if isinstance(raw_artifacts, list):
        artifact_names = [
            entry["name"]
            for entry in raw_artifacts
            if isinstance(entry, dict) and isinstance(entry.get("name"), str)
        ]
    summary = manifest.get("summary")
    inspection = manifest.get("inspection")
    internal_paths = _internal_paths(task)
    return TaskDetailResponse(
        id=task.id,
        type=task.type,
        status=task.status,
        stage=task.stage,
        progress=task.progress,
        style=task.style,
        created_at=task.created_at,
        started_at=task.started_at,
        finished_at=task.finished_at,
        expires_at=task.expires_at,
        error_code=task.error_code,
        message=_task_error_message(task),
        retryable=task.retryable,
        quota_charged=task.quota_charged,
        cleanup_pending=task.cleanup_pending,
        result=TaskResultResponse(
            manifest_available=bool(manifest),
            summary=(
                _safe_payload(summary, internal_paths)
                if isinstance(summary, dict)
                else None
            ),
            artifacts=artifact_names,
        ),
        selected_hdu=task.selected_hdu,
        inspection=(
            _safe_payload(inspection, internal_paths)
            if isinstance(inspection, dict)
            else None
        ),
    )


def _task_error_message(task: Task) -> str | None:
    error_code = task.error_code or ""
    if (
        error_code in DETAILED_TASK_ERROR_CODES
        and isinstance(task.error_message, str)
        and task.error_message
    ):
        safe = _safe_value(task.error_message, _internal_paths(task))
        if isinstance(safe, str):
            return safe[:500]
    return PUBLIC_ERROR_MESSAGES.get(error_code)


def _is_sensitive_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
    tokens = set(normalized.split("_"))
    return (
        normalized in SENSITIVE_KEYS
        or bool(tokens & SENSITIVE_KEYS)
        or normalized.endswith("_path")
    )


def _looks_like_internal_path(value: str, internal_paths: set[str]) -> bool:
    normalized = value.strip()
    lower = normalized.lower()
    return (
        normalized in internal_paths
        or normalized.startswith("/")
        or normalized.startswith("\\\\")
        or normalized.startswith("//")
        or lower.startswith("file://")
        or WINDOWS_DRIVE_PATH.match(normalized) is not None
        or TRAVERSAL_PATH.search(normalized) is not None
        or "/" in normalized
        or "\\" in normalized
    )


def _safe_value(value: object, internal_paths: set[str]) -> object:
    if isinstance(value, dict):
        return {
            str(key): safe_item
            for key, item in value.items()
            if not _is_sensitive_key(str(key))
            if (safe_item := _safe_value(item, internal_paths)) is not _DROP
        }
    if isinstance(value, list):
        return [
            safe_item
            for item in value
            if (safe_item := _safe_value(item, internal_paths)) is not _DROP
        ]
    if isinstance(value, str):
        return (
            "[redacted]"
            if _looks_like_internal_path(value, internal_paths)
            else value
        )
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _DROP


def _safe_payload(
    value: dict[str, object],
    internal_paths: set[str],
    *,
    allowed_keys: set[str] | None = None,
) -> dict[str, object]:
    return {
        key: safe_item
        for key, item in value.items()
        if allowed_keys is None or key in allowed_keys
        if not _is_sensitive_key(key)
        if (safe_item := _safe_value(item, internal_paths)) is not _DROP
    }


def _event_payload(task: Task, event: TaskEvent) -> dict[str, object]:
    payload = dict(event.payload)
    if event.event_type == "task_failed":
        message = _task_error_message(task)
        if message:
            payload.setdefault("message", message)
        if isinstance(task.retryable, bool):
            payload.setdefault("retryable", task.retryable)
    return _safe_payload(
        payload,
        _internal_paths(task),
        allowed_keys=EVENT_KEY_ALLOWLISTS.get(event.event_type),
    )


def _internal_paths(task: Task) -> set[str]:
    return {
        value
        for value in (
            task.input_path,
            task.upload.stored_path if task.upload is not None else None,
        )
        if isinstance(value, str)
    }


def _owned_task_or_response(
    service: TaskService,
    task_id: str,
    client_id: str | None,
    request_ip: str,
) -> Task | JSONResponse:
    if not client_id:
        return _missing_client_response()
    try:
        return service.get_owned(task_id, client_id, request_ip)
    except TaskCreationError as exc:
        return _error_response(exc)


@router.post(
    "/api/tasks/analysis",
    response_model=TaskResponse,
    status_code=201,
    responses={
        status: {"model": TaskErrorResponse}
        for status in (400, 404, 409, 410, 422, 429, 503)
    },
)
def create_analysis_task(
    request: Request,
    payload: AnalysisTaskCreate,
    session: Annotated[Session, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    client_id: Annotated[str | None, Header(alias="X-Starun-Client-Id")] = None,
) -> TaskResponse | JSONResponse:
    if not client_id:
        return _missing_client_response()
    try:
        task = TaskService(session, settings).create_analysis(
            payload.upload_id,
            client_id,
            _request_ip(request),
        )
    except TaskCreationError as exc:
        return _error_response(exc)
    _notify_task_executor(request)
    return _task_response(task)


@router.post(
    "/api/tasks/process",
    response_model=TaskResponse,
    status_code=201,
    responses={
        status: {"model": TaskErrorResponse}
        for status in (400, 404, 409, 410, 422, 429, 503)
    },
)
def create_processing_task(
    request: Request,
    payload: ProcessingTaskCreate,
    session: Annotated[Session, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    client_id: Annotated[str | None, Header(alias="X-Starun-Client-Id")] = None,
) -> TaskResponse | JSONResponse:
    if not client_id:
        return _missing_client_response()
    try:
        task = TaskService(session, settings).create_processing(
            upload_id=payload.upload_id,
            source_task_id=payload.source_task_id,
            style=payload.style,
            client_id=client_id,
            request_ip=_request_ip(request),
        )
    except TaskCreationError as exc:
        return _error_response(exc)
    _notify_task_executor(request)
    return _task_response(task)


@router.get(
    "/api/tasks/{task_id}",
    response_model=TaskDetailResponse,
    responses={status: {"model": TaskErrorResponse} for status in (400, 404, 410, 429, 503)},
)
def get_task(
    request: Request,
    task_id: str,
    session: Annotated[Session, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    client_id: Annotated[str | None, Header(alias="X-Starun-Client-Id")] = None,
) -> TaskDetailResponse | JSONResponse:
    limited = rate_limit_response(request, client_id, "task_lookup")
    if limited is not None:
        return limited
    task = _owned_task_or_response(
        TaskService(session, settings),
        task_id,
        client_id,
        _request_ip(request),
    )
    return task if isinstance(task, JSONResponse) else _task_detail(task)


@router.get(
    "/api/tasks/{task_id}/events",
    response_model=TaskEventsResponse,
    responses={status: {"model": TaskErrorResponse} for status in (400, 404, 410, 422, 429)},
)
def get_task_events(
    request: Request,
    task_id: str,
    session: Annotated[Session, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    after: int = 0,
    client_id: Annotated[str | None, Header(alias="X-Starun-Client-Id")] = None,
) -> TaskEventsResponse | JSONResponse:
    limited = rate_limit_response(request, client_id, "task_events")
    if limited is not None:
        return limited
    task = _owned_task_or_response(
        TaskService(session, settings),
        task_id,
        client_id,
        _request_ip(request),
    )
    if isinstance(task, JSONResponse):
        return task
    if after < 0:
        return _error_response(
            TaskCreationError(
                422,
                "invalid_event_cursor",
                "The event cursor must be zero or greater.",
            )
        )
    events = list(
        session.scalars(
            select(TaskEvent)
            .where(TaskEvent.task_id == task.id, TaskEvent.sequence > after)
            .order_by(TaskEvent.sequence)
            .limit(201)
        )
    )
    page = events[:200]
    return TaskEventsResponse(
        events=[
            TaskEventResponse(
                sequence=event.sequence,
                level=event.level.value,
                event_type=event.event_type,
                payload=_event_payload(task, event),
                created_at=event.created_at,
            )
            for event in page
        ],
        next_after=page[-1].sequence if page else after,
        has_more=len(events) > 200,
    )


@router.post(
    "/api/tasks/{task_id}/cancel",
    response_model=TaskDetailResponse,
    responses={status: {"model": TaskErrorResponse} for status in (400, 404, 410, 429, 503)},
)
def cancel_task(
    request: Request,
    task_id: str,
    session: Annotated[Session, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    client_id: Annotated[str | None, Header(alias="X-Starun-Client-Id")] = None,
) -> TaskDetailResponse | JSONResponse:
    limited = rate_limit_response(request, client_id, "task_cancel")
    if limited is not None:
        return limited
    if not client_id:
        return _missing_client_response()
    try:
        task, notify = TaskService(session, settings).cancel(
            task_id,
            client_id,
            _request_ip(request),
        )
    except TaskCreationError as exc:
        return _error_response(exc)
    if notify:
        _notify_task_executor(request)
    return _task_detail(task)


@router.post(
    "/api/tasks/{task_id}/retry",
    response_model=TaskResponse,
    status_code=201,
    responses={status: {"model": TaskErrorResponse} for status in (400, 404, 410, 429, 503)},
)
def retry_task(
    request: Request,
    task_id: str,
    session: Annotated[Session, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    client_id: Annotated[str | None, Header(alias="X-Starun-Client-Id")] = None,
) -> TaskResponse | JSONResponse:
    limited = rate_limit_response(request, client_id, "task_retry")
    if limited is not None:
        return limited
    if not client_id:
        return _missing_client_response()
    try:
        task = TaskService(session, settings).retry(
            task_id,
            client_id,
            _request_ip(request),
        )
    except TaskCreationError as exc:
        return _error_response(exc)
    _notify_task_executor(request)
    return _task_response(task)


@router.delete(
    "/api/tasks/{task_id}",
    response_model=TaskDetailResponse,
    responses={
        400: {"model": TaskErrorResponse},
        404: {"model": TaskErrorResponse},
        410: {"model": TaskErrorResponse},
        429: {"model": TaskErrorResponse},
        503: {
            "model": TaskErrorResponse,
            "description": "Task cleanup store failure.",
            "content": {
                "application/json": {
                    "examples": {
                        "busy": {
                            "value": {
                                "error_code": "task_store_busy",
                                "message": "The task store is busy. Please retry shortly.",
                                "retryable": True,
                                "quota_charged": False,
                            }
                        },
                        "unavailable": {
                            "value": {
                                "error_code": "task_store_unavailable",
                                "message": "The task store is temporarily unavailable.",
                                "retryable": True,
                                "quota_charged": False,
                            }
                        },
                    }
                }
            },
        },
    },
)
def delete_task(
    request: Request,
    task_id: str,
    session: Annotated[Session, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    client_id: Annotated[str | None, Header(alias="X-Starun-Client-Id")] = None,
) -> TaskDetailResponse | JSONResponse:
    limited = rate_limit_response(request, client_id, "task_delete")
    if limited is not None:
        return limited
    if not client_id:
        return _missing_client_response()
    try:
        task, pending = TaskService(session, settings).delete(
            task_id,
            client_id,
            _request_ip(request),
        )
    except TaskCreationError as exc:
        return _error_response(exc)
    if pending:
        _notify_task_executor(request)
        return JSONResponse(
            status_code=202,
            content=_task_detail(task).model_dump(mode="json"),
        )
    return _task_detail(task)


@router.get(
    "/api/usage",
    response_model=UsageResponse,
    responses={400: {"model": TaskErrorResponse}},
)
def get_usage(
    request: Request,
    session: Annotated[Session, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    client_id: Annotated[str | None, Header(alias="X-Starun-Client-Id")] = None,
) -> UsageResponse | JSONResponse:
    if not client_id:
        return _missing_client_response()
    usage_date, used, remaining = get_daily_usage(
        session,
        settings,
        client_id,
        _request_ip(request),
    )
    return UsageResponse(
        date=usage_date,
        limit=settings.daily_task_limit,
        used=used,
        remaining=remaining,
    )
