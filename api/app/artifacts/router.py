import hashlib
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.artifacts.contracts import ArtifactManifestEntry
from app.artifacts.store import (
    ArtifactPathError,
    ArtifactSizeError,
    ArtifactStore,
    UnsupportedArtifactError,
)
from app.config import Settings
from app.db.models import TaskStatus
from app.db.session import get_db_session
from app.security.rate_limit import rate_limit_response
from app.tasks.schemas import TaskErrorResponse
from app.tasks.service import TaskCreationError, TaskService
from app.uploads.service import get_settings

router = APIRouter(tags=["artifacts"])


def _request_ip(request: Request) -> str:
    return request.client.host if request.client is not None else ""


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    body = TaskErrorResponse(error_code=code, message=message)
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(mode="json", exclude_none=True),
    )


def _manifest_entry(manifest: dict[str, Any], name: str) -> ArtifactManifestEntry | None:
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        return None
    for candidate in artifacts:
        if isinstance(candidate, dict) and candidate.get("name") == name:
            try:
                return ArtifactManifestEntry.model_validate(candidate)
            except ValidationError:
                return None
    return None


@router.get(
    "/api/tasks/{task_id}/artifacts/{name:path}",
    responses={status: {"model": TaskErrorResponse} for status in (400, 404, 410, 429)},
)
def download_artifact(
    request: Request,
    task_id: str,
    name: str,
    session: Annotated[Session, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    client_id: Annotated[str | None, Header(alias="X-Starun-Client-Id")] = None,
) -> Response:
    limited = rate_limit_response(request, client_id, "artifact_download")
    if limited is not None:
        return limited
    if not client_id:
        return _error(400, "missing_client_id", "The client identifier is required.")
    try:
        task = TaskService(session, settings).get_owned(
            task_id,
            client_id,
            _request_ip(request),
        )
    except TaskCreationError as exc:
        return _error(exc.status_code, exc.error_code, exc.message)
    if (
        task.status not in {TaskStatus.COMPLETED, TaskStatus.REVIEW_REQUIRED}
        or task.expires_at is None
        or task.expires_at <= datetime.now(UTC)
        or not isinstance(task.result_manifest, dict)
    ):
        return _error(410, "artifact_unavailable", "The artifact is unavailable.")
    entry = _manifest_entry(task.result_manifest, name)
    if entry is None:
        return _error(404, "artifact_not_found", "The artifact was not found.")

    root = settings.data_root / "tasks" / task.id
    try:
        with ArtifactStore(root, create=False) as store:
            data = store.read_bytes(entry.name)
    except (
        FileNotFoundError,
        ArtifactPathError,
        ArtifactSizeError,
        UnsupportedArtifactError,
        OSError,
    ):
        return _error(410, "artifact_unavailable", "The artifact is unavailable.")
    if len(data) != entry.size or hashlib.sha256(data).hexdigest() != entry.sha256:
        return _error(410, "artifact_unavailable", "The artifact is unavailable.")
    return Response(
        content=data,
        media_type=entry.media_type,
        headers={
            "Content-Length": str(len(data)),
            "Content-Disposition": f'attachment; filename="{entry.name}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )
