from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.tasks.router import router as tasks_router
from app.uploads.middleware import UploadRequestGuardMiddleware
from app.uploads.router import router as uploads_router

app = FastAPI(title="Starun API")
app.add_middleware(UploadRequestGuardMiddleware)
app.include_router(uploads_router)
app.include_router(tasks_router)


@app.exception_handler(RequestValidationError)
async def stable_task_validation_error(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
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
    return await request_validation_exception_handler(request, exc)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
