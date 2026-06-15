import hashlib
import importlib
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.orm import Session

from app.artifacts.store import ArtifactStore
from app.config import Settings
from app.db.models import Task, TaskStatus, TaskType
from app.usage.service import hash_identity


@pytest.fixture(autouse=True)
def reset_rate_limits() -> Generator[None, None, None]:
    try:
        module = importlib.import_module("app.security.rate_limit")
    except ModuleNotFoundError:
        yield
        return
    module.reset_rate_limiters()
    yield
    module.reset_rate_limiters()


def _task(
    db_session: Session,
    settings: Settings,
    task_id: str,
    *,
    status: TaskStatus = TaskStatus.COMPLETED,
) -> Task:
    now = datetime.now(UTC)
    source = settings.data_root / "uploads" / task_id / "input.fits"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"source")
    task = Task(
        id=task_id,
        type=TaskType.ANALYSIS,
        status=status,
        stage=status.value,
        progress=100,
        client_id_hash=hash_identity("test-client"),
        ip_hash=hash_identity("testclient"),
        input_path=str(source),
        quota_charged=True,
        created_at=now - timedelta(hours=1),
        finished_at=now,
        expires_at=now + timedelta(hours=24),
    )
    db_session.add(task)
    db_session.commit()
    return task


def _artifact_task(
    db_session: Session,
    settings: Settings,
    task_id: str,
) -> Task:
    task = _task(db_session, settings, task_id)
    data = b'{"ok":true}\n'
    with ArtifactStore(settings.data_root / "tasks" / task.id) as store:
        entry = store.write_bytes("result.json", data)
    task.result_manifest = {"artifacts": [entry.model_dump(mode="json")]}
    db_session.commit()
    return task


def _assert_rate_limited(response: Response) -> None:
    assert response.status_code == 429
    body = response.json()
    assert body == {
        "error_code": "rate_limit_exceeded",
        "message": "Too many requests. Please retry later.",
        "retryable": True,
        "quota_charged": False,
    }
    assert int(response.headers["retry-after"]) >= 1


def test_task_lookup_allows_exactly_60_requests_then_returns_429(
    client: TestClient,
    headers: dict[str, str],
    db_session: Session,
    settings: Settings,
) -> None:
    task = _task(db_session, settings, "lookup-limit")

    for _ in range(60):
        assert client.get(f"/api/tasks/{task.id}", headers=headers).status_code == 200

    _assert_rate_limited(client.get(f"/api/tasks/{task.id}", headers=headers))


def test_missing_client_id_requests_consume_ip_bucket_before_validation(
    client: TestClient,
    db_session: Session,
    settings: Settings,
) -> None:
    task = _task(db_session, settings, "missing-id-limit")

    for _ in range(60):
        response = client.get(f"/api/tasks/{task.id}")
        assert response.status_code == 400
        assert response.json()["error_code"] == "missing_client_id"

    _assert_rate_limited(client.get(f"/api/tasks/{task.id}"))


def test_missing_id_and_rotated_ids_share_same_ip_bucket(
    client: TestClient,
    db_session: Session,
    settings: Settings,
) -> None:
    task = _task(db_session, settings, "missing-rotation-limit")

    for _ in range(30):
        assert client.get(f"/api/tasks/{task.id}").status_code == 400
    for index in range(30):
        client_id = f"mixed-{index}"
        task.client_id_hash = hash_identity(client_id)
        db_session.commit()
        assert client.get(
            f"/api/tasks/{task.id}",
            headers={"X-Starun-Client-Id": client_id},
        ).status_code == 200

    _assert_rate_limited(client.get(f"/api/tasks/{task.id}"))


@pytest.mark.parametrize(
    ("method", "suffix"),
    [
        ("get", "/events"),
        ("post", "/cancel"),
        ("post", "/retry"),
        ("delete", ""),
    ],
)
def test_task_action_buckets_are_route_specific(
    client: TestClient,
    headers: dict[str, str],
    db_session: Session,
    settings: Settings,
    method: str,
    suffix: str,
) -> None:
    task = _task(db_session, settings, f"route-{method}-{suffix.replace('/', '')}")

    for _ in range(60):
        assert client.get(f"/api/tasks/{task.id}", headers=headers).status_code == 200

    response = getattr(client, method)(f"/api/tasks/{task.id}{suffix}", headers=headers)

    assert response.status_code != 429


@pytest.mark.parametrize(
    ("method", "suffix"),
    [
        ("get", "/events"),
        ("post", "/cancel"),
        ("post", "/retry"),
        ("delete", ""),
    ],
)
def test_each_task_action_is_rate_limited_independently(
    client: TestClient,
    headers: dict[str, str],
    db_session: Session,
    settings: Settings,
    method: str,
    suffix: str,
) -> None:
    settings.daily_task_limit = 100
    task = _task(db_session, settings, f"exhaust-{method}-{suffix.replace('/', '')}")
    request = getattr(client, method)

    for _ in range(60):
        assert request(f"/api/tasks/{task.id}{suffix}", headers=headers).status_code != 429

    _assert_rate_limited(request(f"/api/tasks/{task.id}{suffix}", headers=headers))


def test_artifact_download_has_an_independent_rate_limit(
    client: TestClient,
    headers: dict[str, str],
    db_session: Session,
    settings: Settings,
) -> None:
    task = _artifact_task(db_session, settings, "artifact-limit")
    url = f"/api/tasks/{task.id}/artifacts/result.json"

    for _ in range(60):
        assert client.get(url, headers=headers).status_code == 200

    _assert_rate_limited(client.get(url, headers=headers))


def test_rate_limit_identity_uses_client_header_and_route_not_forwarded_headers(
    client: TestClient,
    db_session: Session,
    settings: Settings,
) -> None:
    task = _task(db_session, settings, "identity-limit")
    first = {
        "X-Starun-Client-Id": "first-client",
        "X-Forwarded-For": "203.0.113.10",
    }
    second = {
        "X-Starun-Client-Id": "second-client",
        "X-Forwarded-For": "203.0.113.10",
    }
    task.client_id_hash = hash_identity("first-client")
    db_session.commit()

    for _ in range(60):
        assert client.get(f"/api/tasks/{task.id}", headers=first).status_code == 200
    _assert_rate_limited(client.get(f"/api/tasks/{task.id}", headers=first))

    task.client_id_hash = hash_identity("second-client")
    db_session.commit()
    _assert_rate_limited(client.get(f"/api/tasks/{task.id}", headers=second))


def test_rotating_client_ids_cannot_bypass_peer_ip_rate_limit(
    client: TestClient,
    db_session: Session,
    settings: Settings,
) -> None:
    task = _task(db_session, settings, "rotation-limit")

    for index in range(60):
        client_id = f"rotated-{index}"
        task.client_id_hash = hash_identity(client_id)
        db_session.commit()
        assert client.get(
            f"/api/tasks/{task.id}",
            headers={"X-Starun-Client-Id": client_id},
        ).status_code == 200

    final_id = "rotated-final"
    task.client_id_hash = hash_identity(final_id)
    db_session.commit()
    _assert_rate_limited(
        client.get(
            f"/api/tasks/{task.id}",
            headers={"X-Starun-Client-Id": final_id},
        )
    )


def test_rate_limit_store_has_bounded_size_and_is_resettable() -> None:
    try:
        module = importlib.import_module("app.security.rate_limit")
    except ModuleNotFoundError:
        pytest.fail("Task 12 rate limiter is not implemented")
    limiter = module.TokenBucketLimiter(capacity=2, refill_seconds=60.0, max_entries=3)

    for index in range(10):
        limiter.check((f"client-{index}", "lookup"))

    assert limiter.entry_count <= 3
    limiter.reset()
    assert limiter.entry_count == 0


def test_artifact_route_rejects_symlinked_task_directory_escape(
    client: TestClient,
    headers: dict[str, str],
    db_session: Session,
    settings: Settings,
    tmp_path: Path,
) -> None:
    task = _artifact_task(db_session, settings, "artifact-root-escape")
    task_dir = settings.data_root / "tasks" / task.id
    outside = tmp_path / "outside-task"
    outside.mkdir()
    data = b'{"outside":true}\n'
    (outside / "result.json").write_bytes(data)
    task.result_manifest = {
        "artifacts": [
            {
                "name": "result.json",
                "media_type": "application/json",
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "demo": True,
            }
        ]
    }
    db_session.commit()
    for child in task_dir.iterdir():
        child.unlink()
    task_dir.rmdir()
    task_dir.symlink_to(outside, target_is_directory=True)

    response = client.get(
        f"/api/tasks/{task.id}/artifacts/result.json",
        headers=headers,
    )

    assert response.status_code == 410
    assert response.json()["error_code"] == "artifact_unavailable"
    assert (outside / "result.json").read_bytes() == data


@pytest.mark.parametrize("symlink_component", ["tasks", "data-parent"])
def test_artifact_route_rejects_symlinked_parent_components(
    client: TestClient,
    headers: dict[str, str],
    db_session: Session,
    settings: Settings,
    tmp_path: Path,
    symlink_component: str,
) -> None:
    task = _artifact_task(db_session, settings, f"parent-{symlink_component}")
    data = b'{"outside":true}\n'
    outside = tmp_path / f"outside-{symlink_component}"
    outside_task = outside / "tasks" / task.id
    outside_task.mkdir(parents=True)
    (outside_task / "result.json").write_bytes(data)
    task.result_manifest = {
        "artifacts": [
            {
                "name": "result.json",
                "media_type": "application/json",
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "demo": True,
            }
        ]
    }
    db_session.commit()
    if symlink_component == "tasks":
        real_tasks = settings.data_root / "tasks"
        for child in real_tasks.iterdir():
            if child.is_dir():
                for artifact in child.iterdir():
                    artifact.unlink()
                child.rmdir()
        real_tasks.rmdir()
        real_tasks.symlink_to(outside / "tasks", target_is_directory=True)
    else:
        original_root = settings.data_root
        moved_root = tmp_path / "moved-data"
        original_root.rename(moved_root)
        original_root.symlink_to(outside, target_is_directory=True)
        outside_task = outside / "tasks" / task.id

    response = client.get(
        f"/api/tasks/{task.id}/artifacts/result.json",
        headers=headers,
    )

    assert response.status_code == 410
    assert (outside_task / "result.json").read_bytes() == data


def test_artifact_route_rejects_manifest_path_escape(
    client: TestClient,
    headers: dict[str, str],
    db_session: Session,
    settings: Settings,
    tmp_path: Path,
) -> None:
    task = _artifact_task(db_session, settings, "artifact-manifest-escape")
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"outside")
    task.result_manifest = {
        "artifacts": [
            {
                "name": "../outside.json",
                "media_type": "application/json",
                "size": 7,
                "sha256": hashlib.sha256(b"outside").hexdigest(),
                "demo": True,
            }
        ]
    }
    db_session.commit()

    response = client.get(
        f"/api/tasks/{task.id}/artifacts/..%2Foutside.json",
        headers=headers,
    )

    assert response.status_code in {404, 410}
    assert outside.read_bytes() == b"outside"


def test_no_task_list_route(client: TestClient, headers: dict[str, str]) -> None:
    assert client.get("/api/tasks", headers=headers).status_code == 404
