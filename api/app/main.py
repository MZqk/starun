from fastapi import FastAPI

from app.tasks.router import router as tasks_router
from app.uploads.middleware import UploadRequestGuardMiddleware
from app.uploads.router import router as uploads_router

app = FastAPI(title="Starun API")
app.add_middleware(UploadRequestGuardMiddleware)
app.include_router(uploads_router)
app.include_router(tasks_router)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
