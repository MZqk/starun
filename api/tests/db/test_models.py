from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app.db.models import (
    DailyUsage,
    EventLevel,
    ProcessingStyle,
    Task,
    TaskEvent,
    TaskStatus,
    TaskType,
    Upload,
    UploadStatus,
)


def test_upload_and_task_defaults(db_session) -> None:
    upload = Upload(
        id="upload-1",
        client_id_hash="client",
        ip_hash="ip",
        original_file_name="m31.fits",
        stored_path="/tmp/input.fits",
        size_bytes=128,
        status=UploadStatus.READY,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    task = Task(
        id="task-1",
        type=TaskType.ANALYSIS,
        status=TaskStatus.QUEUED,
        client_id_hash="client",
        ip_hash="ip",
        upload_id=upload.id,
        quota_charged=True,
    )
    db_session.add_all([upload, task])
    db_session.commit()
    db_session.refresh(task)

    assert task.progress == 0
    assert task.retryable is False


def test_task_event_sequence_is_unique_per_task(db_session) -> None:
    task = Task(
        id="task-events",
        type=TaskType.ANALYSIS,
        status=TaskStatus.QUEUED,
        client_id_hash="client",
        ip_hash="ip",
    )
    task.events.extend(
        [
            TaskEvent(
                sequence=1,
                level=EventLevel.INFO,
                event_type="queued",
                payload={"position": 1},
            ),
            TaskEvent(
                sequence=1,
                level=EventLevel.WARNING,
                event_type="duplicate",
            ),
        ]
    )
    db_session.add(task)

    with pytest.raises(IntegrityError):
        db_session.commit()


def test_daily_usage_key_is_unique(db_session) -> None:
    usage_date = date(2026, 6, 11)
    db_session.add(
        DailyUsage(
            date=usage_date,
            client_id_hash="client",
            ip_hash="ip",
        )
    )
    db_session.commit()

    usage = db_session.query(DailyUsage).one()
    assert usage.count == 0

    db_session.add(
        DailyUsage(
            date=usage_date,
            client_id_hash="client",
            ip_hash="ip",
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_sqlite_foreign_keys_are_enforced(db_session) -> None:
    db_session.add(
        Task(
            id="task-missing-upload",
            type=TaskType.ANALYSIS,
            status=TaskStatus.QUEUED,
            client_id_hash="client",
            ip_hash="ip",
            upload_id="missing-upload",
        )
    )

    with pytest.raises(IntegrityError):
        db_session.commit()


def test_settings_use_starun_environment_prefix(monkeypatch, tmp_path) -> None:
    from app.config import Settings

    database_url = f"sqlite:///{tmp_path / 'settings.db'}"
    data_root = tmp_path / "data"
    monkeypatch.setenv("STARUN_DATABASE_URL", database_url)
    monkeypatch.setenv("STARUN_DATA_ROOT", str(data_root))
    monkeypatch.setenv("STARUN_DAILY_TASK_LIMIT", "9")

    settings = Settings()

    assert settings.database_url == database_url
    assert settings.data_root == data_root
    assert settings.daily_task_limit == 9


def test_enum_values_persist_as_lowercase_contract_strings(db_session) -> None:
    upload = Upload(
        id="upload-enums",
        client_id_hash="client",
        ip_hash="ip",
        original_file_name="m42.fits",
        stored_path="/tmp/m42.fits",
        size_bytes=256,
        status=UploadStatus.VALIDATING,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    task = Task(
        id="task-enums",
        type=TaskType.PROCESSING,
        status=TaskStatus.RUNNING,
        style=ProcessingStyle.ARTISTIC,
        client_id_hash="client",
        ip_hash="ip",
        upload=upload,
    )
    task.events.append(
        TaskEvent(
            sequence=1,
            level=EventLevel.ERROR,
            event_type="failed",
        )
    )
    db_session.add(task)
    db_session.commit()

    assert db_session.execute(
        text("SELECT status FROM uploads WHERE id = 'upload-enums'")
    ).scalar_one() == "validating"
    assert db_session.execute(
        text("SELECT type || ':' || status || ':' || style FROM tasks WHERE id = 'task-enums'")
    ).scalar_one() == "processing:running:artistic"
    assert db_session.execute(
        text("SELECT level FROM task_events WHERE task_id = 'task-enums'")
    ).scalar_one() == "error"
    assert task.events[0].payload == {}


def test_initial_migration_creates_expected_tables(monkeypatch, tmp_path) -> None:
    api_root = Path(__file__).resolve().parents[2]
    database_url = f"sqlite:///{tmp_path / 'migration.db'}"
    monkeypatch.setenv("STARUN_DATABASE_URL", database_url)

    alembic_config = Config(api_root / "alembic.ini")
    command.upgrade(alembic_config, "head")

    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        assert {
            "alembic_version",
            "daily_usage",
            "task_events",
            "tasks",
            "uploads",
        } == set(inspector.get_table_names())
        assert {index["name"] for index in inspector.get_indexes("uploads")} == {
            "ix_uploads_status_expires_at"
        }
        assert {index["name"] for index in inspector.get_indexes("tasks")} == {
            "ix_tasks_expires_at",
            "ix_tasks_status_created_at",
        }
        assert {index["name"] for index in inspector.get_indexes("task_events")} == {
            "ix_task_events_task_sequence"
        }
        assert {
            constraint["name"]
            for constraint in inspector.get_unique_constraints("task_events")
        } == {"uq_task_event_sequence"}
        assert {
            constraint["name"]
            for constraint in inspector.get_unique_constraints("daily_usage")
        } == {"uq_daily_usage_client"}
        assert {
            foreign_key["referred_table"]
            for foreign_key in inspector.get_foreign_keys("tasks")
        } == {
            "tasks",
            "uploads",
        }
        event_foreign_key = inspector.get_foreign_keys("task_events")[0]
        assert event_foreign_key["referred_table"] == "tasks"
        assert event_foreign_key["options"] == {"ondelete": "CASCADE"}
    finally:
        engine.dispose()


def test_test_fixtures_are_isolated(settings, data_root, headers, client) -> None:
    assert settings.database_url.endswith("/test.db")
    assert settings.data_root == data_root
    assert data_root.is_dir()
    assert headers == {"X-Starun-Client-Id": "test-client"}
    assert client.get("/api/health", headers=headers).json() == {"status": "ok"}
