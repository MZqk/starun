from typing import Annotated

from fastapi import APIRouter, Depends, File, Header, Request, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.session import get_db_session
from app.uploads.errors import (
    UploadError,
    missing_client_id_error,
)
from app.uploads.schemas import UploadErrorResponse, UploadResponse
from app.uploads.service import (
    DiskUsage,
    Inspector,
    UploadService,
    get_disk_usage,
    get_inspector,
    get_settings,
)

router = APIRouter(prefix="/api/uploads", tags=["uploads"])


def _error_response(error: UploadError) -> JSONResponse:
    body = UploadErrorResponse(
        error_code=error.error_code,
        message=error.message,
        diagnostic_id=error.diagnostic_id,
    )
    return JSONResponse(status_code=error.status_code, content=body.model_dump(mode="json"))


@router.post(
    "",
    response_model=UploadResponse,
    status_code=201,
    responses={400: {"model": UploadErrorResponse}, 413: {"model": UploadErrorResponse}},
)
async def create_upload(
    request: Request,
    file: Annotated[UploadFile, File()],
    session: Annotated[Session, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    inspector: Annotated[Inspector, Depends(get_inspector)],
    disk_usage: Annotated[DiskUsage, Depends(get_disk_usage)],
    client_id: Annotated[str | None, Header(alias="X-Starun-Client-Id")] = None,
) -> UploadResponse | JSONResponse:
    if not client_id:
        await file.close()
        return _error_response(missing_client_id_error())

    request_ip = request.client.host if request.client is not None else ""
    service = UploadService(session, settings, inspector, disk_usage)
    try:
        upload, inspection = await service.create(
            file,
            client_id,
            request_ip,
            declared_size_bytes=file.size,
        )
    except UploadError as exc:
        return _error_response(exc)
    return UploadResponse(
        upload_id=upload.id,
        status=upload.status.value,
        expires_at=upload.expires_at,
        inspection=inspection,
    )
