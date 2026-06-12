from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import Task
from app.db.session import get_db_session
from app.tasks.schemas import (
    AnalysisTaskCreate,
    ProcessingTaskCreate,
    TaskErrorResponse,
    TaskResponse,
    UsageResponse,
)
from app.tasks.service import TaskCreationError, TaskService
from app.uploads.errors import missing_client_id_error
from app.uploads.service import get_settings
from app.usage.service import get_daily_usage

router = APIRouter(tags=["tasks"])


def _request_ip(request: Request) -> str:
    return request.client.host if request.client is not None else ""


def _error_response(error: TaskCreationError) -> JSONResponse:
    body = TaskErrorResponse(
        error_code=error.error_code,
        message=error.message,
        retryable=error.retryable,
        quota_charged=False,
    )
    return JSONResponse(status_code=error.status_code, content=body.model_dump(mode="json"))


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
    return _task_response(task)


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
