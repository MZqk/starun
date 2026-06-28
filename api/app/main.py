from contextlib import AbstractAsyncContextManager
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
import logging
import os
from typing import cast

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session, sessionmaker
from starlette.middleware.cors import CORSMiddleware

from app.artifacts.router import router as artifacts_router
from app.cleanup.service import CleanupScheduler, recover_interrupted_uploads
from app.config import Settings
from app.db import session as db_session_module
from app.tasks.executor import SerialTaskExecutor
from app.tasks.recovery import recover_interrupted_tasks
from app.tasks.router import router as tasks_router
from app.uploads.middleware import UploadRequestGuardMiddleware
from app.uploads.router import router as uploads_router


load_dotenv()

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DEBUG_LOGGERS = (
    "app",
    "agents",
    "agents.extensions",
    "agents.sandbox",
    "httpcore",
    "httpx",
    "openai",
    "openai._base_client",
    "openai.agents",
    "openai.agents.tracing",
    "openai-agents",
)


def _resolve_log_level() -> int:
    configured = os.getenv("STARUN_LOG_LEVEL")
    if configured:
        return getattr(logging, configured.upper(), logging.INFO)
    uvicorn_level = logging.getLogger("uvicorn.error").getEffectiveLevel()
    if uvicorn_level <= logging.DEBUG:
        return logging.DEBUG
    return logging.INFO


def configure_logging() -> int:
    level = _resolve_log_level()
    logging.basicConfig(
        level=level,
        format=_LOG_FORMAT,
        force=True,
    )
    child_level = logging.DEBUG if level <= logging.DEBUG else logging.INFO
    for name in _DEBUG_LOGGERS:
        logging.getLogger(name).setLevel(child_level)
    return level


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
    active_settings = settings or Settings()
    application.add_middleware(
        CORSMiddleware,
        allow_origins=active_settings.allowed_web_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Accept", "Content-Type", "X-Starun-Client-Id"],
        expose_headers=["Content-Disposition", "Content-Length"],
        max_age=600,
    )
    application.include_router(uploads_router)
    application.include_router(tasks_router)
    application.include_router(artifacts_router)
    application.add_exception_handler(
        RequestValidationError,
        stable_task_validation_error,
    )
    application.add_api_route("/api/health", health, methods=["GET"])
    return application


configure_logging()
app = create_app()
