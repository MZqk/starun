import json
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from starlette.formparsers import MultiPartParser
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import Settings
from app.uploads.errors import (
    UploadError,
    insufficient_storage_error,
    upload_too_large_error,
)
from app.uploads.service import DiskUsage, get_disk_usage, get_settings

MAX_MULTIPART_OVERHEAD_BYTES = 1024 * 1024
MULTIPART_SPOOL_THRESHOLD_BYTES = MultiPartParser.spool_max_size


class _RejectUploadRequest(Exception):
    def __init__(self, error: UploadError) -> None:
        self.error = error


def _nearest_existing_parent(path: Path) -> Path:
    candidate = path
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate


def _resolve_dependency(scope: Scope, dependency: Callable[..., Any]) -> Any:
    fastapi_app = scope["app"]
    override = fastapi_app.dependency_overrides.get(dependency)
    return override() if override is not None else dependency()


def _error_body(error: UploadError) -> bytes:
    return json.dumps(
        {
            "error_code": error.error_code,
            "message": error.message,
            "retryable": error.retryable,
            "quota_charged": False,
            "diagnostic_id": error.diagnostic_id,
        }
    ).encode("utf-8")


async def _send_error(send: Send, error: UploadError) -> None:
    body = _error_body(error)
    await send(
        {
            "type": "http.response.start",
            "status": error.status_code,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class UploadRequestGuardMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"] != "/api/uploads":
            await self.app(scope, receive, send)
            return

        settings: Settings = _resolve_dependency(scope, get_settings)
        disk_usage: DiskUsage = _resolve_dependency(scope, get_disk_usage)
        request_limit = settings.max_upload_bytes + MAX_MULTIPART_OVERHEAD_BYTES
        temp_path = _nearest_existing_parent(Path(tempfile.gettempdir()))

        content_length = next(
            (
                value
                for name, value in scope["headers"]
                if name.lower() == b"content-length"
            ),
            None,
        )
        initial_reservation = 0
        if content_length is not None:
            try:
                declared_request_bytes = int(content_length)
                if declared_request_bytes > request_limit:
                    await _send_error(send, upload_too_large_error())
                    return
                if declared_request_bytes >= 0:
                    initial_reservation = declared_request_bytes
            except ValueError:
                pass

        try:
            self._ensure_disk_space(
                disk_usage,
                temp_path,
                settings.min_free_disk_bytes,
                initial_reservation,
            )
            received_bytes = 0
            spool_rolled_over = False
            rejection: UploadError | None = None

            async def guarded_receive() -> Message:
                nonlocal received_bytes, rejection, spool_rolled_over
                message = await receive()
                if message["type"] != "http.request":
                    return message

                body = message.get("body", b"")
                received_bytes += len(body)
                if received_bytes > request_limit:
                    rejection = upload_too_large_error()
                    return {"type": "http.disconnect"}
                try:
                    crossed_spool_threshold = (
                        not spool_rolled_over
                        and received_bytes > MULTIPART_SPOOL_THRESHOLD_BYTES
                    )
                    reservation_bytes = 0
                    if spool_rolled_over:
                        reservation_bytes = len(body)
                    elif crossed_spool_threshold:
                        reservation_bytes = received_bytes
                    self._ensure_disk_space(
                        disk_usage,
                        temp_path,
                        settings.min_free_disk_bytes,
                        reservation_bytes,
                    )
                    if crossed_spool_threshold:
                        spool_rolled_over = True
                except _RejectUploadRequest as exc:
                    rejection = exc.error
                    return {"type": "http.disconnect"}
                return message

            async def guarded_send(message: Message) -> None:
                if rejection is None:
                    await send(message)

            await self.app(scope, guarded_receive, guarded_send)
            if rejection is not None:
                await _send_error(send, rejection)
        except _RejectUploadRequest as exc:
            await _send_error(send, exc.error)

    @staticmethod
    def _ensure_disk_space(
        disk_usage: DiskUsage,
        disk_path: Path,
        min_free_disk_bytes: int,
        reservation_bytes: int,
    ) -> None:
        free_bytes = disk_usage(disk_path).free
        if free_bytes < min_free_disk_bytes + reservation_bytes:
            raise _RejectUploadRequest(insufficient_storage_error())
