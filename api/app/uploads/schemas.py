from datetime import datetime

from pydantic import BaseModel

from app.fits.schemas import FitsInspection


class UploadResponse(BaseModel):
    upload_id: str
    status: str
    expires_at: datetime
    inspection: FitsInspection


class UploadErrorResponse(BaseModel):
    error_code: str
    message: str
    retryable: bool = False
    quota_charged: bool = False
    diagnostic_id: str | None = None
