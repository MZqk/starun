from contextlib import AbstractAsyncContextManager
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import cast

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session, sessionmaker

from app.artifacts.router import router as artifacts_router
from app.cleanup.service import CleanupScheduler, recover_interrupted_uploads
from app.config import Settings
from app.db import session as db_session_module
from app.tasks.executor import SerialTaskExecutor
from app.tasks.recovery import recover_interrupted_tasks
from app.tasks.router import router as tasks_router
from app.uploads.middleware import UploadRequestGuardMiddleware
from app.uploads.router import router as uploads_router


def _application_session_factory() -> sessionmaker[Session]:
    return db_session_module._application_session_factory


def build_lifespan(
    *,
    session_factory: sessionmaker[Session] | None = None,
    settings: Settings | None = None,
    executor_factory: Callable[[], SerialTaskExecutor] | None = None,
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        active_session_factory = session_factory or _application_session_factory()
        active_settings = settings or Settings()
        executor = (
            executor_factory()
            if executor_factory is not None
            else SerialTaskExecutor(
                active_session_factory,
                active_settings,
            )
        )
        executor_settings = cast(
            Settings,
            settings or getattr(executor, "_settings", active_settings),
        )
        cleanup_scheduler = CleanupScheduler(executor.session_factory, executor_settings)
        recover_interrupted_tasks(executor.session_factory)
        recover_interrupted_uploads(executor.session_factory)
        cleanup_scheduler.start()
        executor.start()
        application.state.task_executor = executor
        application.state.cleanup_scheduler = cleanup_scheduler
        try:
            yield
        finally:
            await cleanup_scheduler.stop()
            await executor.stop()

    return lifespan


async def stable_task_validation_error(
    request: Request,
    exc: Exception,
) -> Response:
    if not isinstance(exc, RequestValidationError):
        raise exc
    if request.url.path in {"/api/tasks/analysis", "/api/tasks/process"}:
        return JSONResponse(
            status_code=422,
            content={
                "error_code": "invalid_request",
                "message": "The request body is invalid.",
                "retryable": False,
                "quota_charged": False,
            },
        )
    if request.url.path.startswith("/api/tasks/") and request.url.path.endswith("/events"):
        return JSONResponse(
            status_code=422,
            content={
                "error_code": "invalid_event_cursor",
                "message": "The event cursor must be zero or greater.",
                "retryable": False,
                "quota_charged": False,
            },
        )
    return await request_validation_exception_handler(request, exc)


def health() -> dict[str, str]:
    return {"status": "ok"}


def create_app(
    *,
    session_factory: sessionmaker[Session] | None = None,
    settings: Settings | None = None,
    executor_factory: Callable[[], SerialTaskExecutor] | None = None,
) -> FastAPI:
    application = FastAPI(
        title="Starun API",
        lifespan=build_lifespan(
            session_factory=session_factory,
            settings=settings,
            executor_factory=executor_factory,
        ),
    )
    application.add_middleware(UploadRequestGuardMiddleware)
    application.include_router(uploads_router)
    application.include_router(tasks_router)
    application.include_router(artifacts_router)
    application.add_exception_handler(
        RequestValidationError,
        stable_task_validation_error,
    )
    application.add_api_route("/api/health", health, methods=["GET"])
    return application


app = create_app()
