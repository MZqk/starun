from fastapi import FastAPI

from app.uploads.router import router as uploads_router

app = FastAPI(title="Starun API")
app.include_router(uploads_router)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
