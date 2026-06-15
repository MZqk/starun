from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def test_task_cleanup_columns_are_migrated(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = tmp_path / "migrated.db"
    monkeypatch.setenv("STARUN_DATABASE_URL", f"sqlite:///{database}")
    config = Config("alembic.ini")

    command.upgrade(config, "head")

    columns = {
        column["name"]
        for column in inspect(create_engine(f"sqlite:///{database}")).get_columns("tasks")
    }
    assert {
        "delete_requested_at",
        "cleanup_pending",
        "cleanup_error",
        "cleanup_plan",
    } <= columns
    upload_columns = {
        column["name"]
        for column in inspect(create_engine(f"sqlite:///{database}")).get_columns("uploads")
    }
    assert {"cleanup_pending", "cleanup_error", "cleanup_plan"} <= upload_columns


def test_task_cleanup_migration_defaults_and_roundtrip_preserve_task(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = tmp_path / "roundtrip.db"
    database_url = f"sqlite:///{database}"
    monkeypatch.setenv("STARUN_DATABASE_URL", database_url)
    config = Config("alembic.ini")
    engine = create_engine(database_url)

    command.upgrade(config, "0001_initial")
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO tasks (
                    id, type, status, client_id_hash, ip_hash, created_at
                ) VALUES (
                    'preserved-task', 'analysis', 'queued', 'client', 'ip',
                    '2026-06-12 08:30:00'
                )
                """
            )
        )

    command.upgrade(config, "head")
    columns = {
        column["name"]: column
        for column in inspect(engine).get_columns("tasks")
    }
    assert columns["delete_requested_at"]["nullable"] is True
    assert columns["cleanup_pending"]["nullable"] is False
    assert columns["cleanup_pending"]["default"] in {"0", "(0)"}
    assert columns["cleanup_error"]["nullable"] is True
    assert columns["cleanup_plan"]["nullable"] is True
    upload_columns = {
        column["name"]: column
        for column in inspect(engine).get_columns("uploads")
    }
    assert upload_columns["cleanup_pending"]["nullable"] is False
    assert upload_columns["cleanup_pending"]["default"] in {"0", "(0)"}
    assert upload_columns["cleanup_error"]["nullable"] is True
    assert upload_columns["cleanup_plan"]["nullable"] is True
    with engine.connect() as connection:
        assert connection.execute(
            text(
                """
                SELECT id, type, status, client_id_hash, ip_hash, cleanup_pending
                FROM tasks WHERE id = 'preserved-task'
                """
            )
        ).one() == (
            "preserved-task",
            "analysis",
            "queued",
            "client",
            "ip",
            0,
        )

    command.downgrade(config, "0001_initial")
    downgraded_columns = {
        column["name"] for column in inspect(engine).get_columns("tasks")
    }
    assert "cleanup_pending" not in downgraded_columns
    downgraded_upload_columns = {
        column["name"] for column in inspect(engine).get_columns("uploads")
    }
    assert "cleanup_pending" not in downgraded_upload_columns
    with engine.connect() as connection:
        assert connection.execute(
            text(
                """
                SELECT id, type, status, client_id_hash, ip_hash
                FROM tasks WHERE id = 'preserved-task'
                """
            )
        ).one() == (
            "preserved-task",
            "analysis",
            "queued",
            "client",
            "ip",
        )

    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.execute(
            text(
                """
                SELECT id, type, status, cleanup_pending
                FROM tasks WHERE id = 'preserved-task'
                """
            )
        ).one() == ("preserved-task", "analysis", "queued", 0)
    engine.dispose()
