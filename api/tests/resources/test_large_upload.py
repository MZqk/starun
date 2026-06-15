import asyncio
import multiprocessing
import os
import resource
import sys
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any, cast

from fastapi import UploadFile
from starlette.types import Message, Receive, Scope, Send

from app.config import Settings
from app.main import app
from app.uploads.service import UploadService, get_settings


NEAR_LIMIT_BYTES = 499 * 1024 * 1024
MAX_RSS_GROWTH_BYTES = 96 * 1024 * 1024


def _max_rss_bytes() -> int:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(rss if sys.platform == "darwin" else rss * 1024)


def _run_sparse_upload(source_path: str, result_pipe: Connection) -> None:
    settings = Settings(
        max_upload_bytes=500 * 1024 * 1024,
        min_free_disk_bytes=0,
    )
    observed: dict[str, int | bool] = {}

    async def inspect_spooled_upload(
        _service: UploadService,
        file: UploadFile,
        _client_id: str,
        _request_ip: str,
        *,
        declared_size_bytes: int | None = None,
    ) -> object:
        observed["declared_size"] = declared_size_bytes or 0
        observed["rolled"] = bool(getattr(file.file, "_rolled", False))
        observed["file_size"] = os.fstat(file.file.fileno()).st_size
        await file.close()
        raise RuntimeError("stop after multipart spooling")

    original_create = UploadService.create
    app.dependency_overrides[get_settings] = lambda: settings
    UploadService.create = inspect_spooled_upload  # type: ignore[method-assign]
    boundary = b"starun-resource-boundary"
    prefix = (
        b"--"
        + boundary
        + b"\r\n"
        + b'Content-Disposition: form-data; name="file"; filename="near-limit.fits"\r\n'
        + b"Content-Type: application/fits\r\n\r\n"
    )
    suffix = b"\r\n--" + boundary + b"--\r\n"
    content_length = len(prefix) + NEAR_LIMIT_BYTES + len(suffix)
    scope = cast(
        Scope,
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/uploads",
            "raw_path": b"/api/uploads",
            "query_string": b"",
            "root_path": "",
            "headers": [
                (b"content-type", b"multipart/form-data; boundary=" + boundary),
                (b"content-length", str(content_length).encode("ascii")),
                (b"x-starun-client-id", b"resource-memory-client"),
            ],
            "client": ("testclient", 123),
            "server": ("testserver", 80),
        },
    )

    async def exercise() -> None:
        sent_prefix = False
        sent_suffix = False
        with Path(source_path).open("rb") as handle:

            async def receive() -> Message:
                nonlocal sent_prefix, sent_suffix
                if not sent_prefix:
                    sent_prefix = True
                    return {"type": "http.request", "body": prefix, "more_body": True}
                chunk = handle.read(1024 * 1024)
                if chunk:
                    return {"type": "http.request", "body": chunk, "more_body": True}
                if not sent_suffix:
                    sent_suffix = True
                    return {"type": "http.request", "body": suffix, "more_body": False}
                return {"type": "http.disconnect"}

            async def send(_message: Message) -> None:
                pass

            try:
                await app(scope, cast(Receive, receive), cast(Send, send))
            except RuntimeError as exc:
                if str(exc) != "stop after multipart spooling":
                    raise

    baseline_rss = _max_rss_bytes()
    try:
        asyncio.run(exercise())
        result_pipe.send(
            {
                "observed": observed,
                "rss_growth": _max_rss_bytes() - baseline_rss,
            }
        )
    except BaseException as exc:
        result_pipe.send({"error": repr(exc)})
    finally:
        UploadService.create = original_create  # type: ignore[method-assign]
        app.dependency_overrides.pop(get_settings, None)
        result_pipe.close()


def test_near_limit_sparse_upload_has_bounded_process_rss(tmp_path: Path) -> None:
    source = tmp_path / "near-limit.fits"
    with source.open("wb") as handle:
        handle.truncate(NEAR_LIMIT_BYTES)

    context = multiprocessing.get_context("spawn")
    parent_pipe, child_pipe = context.Pipe(duplex=False)
    process = context.Process(
        target=_run_sparse_upload,
        args=(str(source), child_pipe),
    )
    process.start()
    child_pipe.close()
    assert parent_pipe.poll(30), "upload worker did not report within 30 seconds"
    result: dict[str, Any] = parent_pipe.recv()
    process.join(timeout=5)
    assert process.exitcode == 0
    assert "error" not in result
    assert result["observed"] == {
        "declared_size": NEAR_LIMIT_BYTES,
        "rolled": True,
        "file_size": NEAR_LIMIT_BYTES,
    }
    assert result["rss_growth"] < MAX_RSS_GROWTH_BYTES
