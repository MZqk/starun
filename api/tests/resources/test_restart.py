from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.models import Task, TaskStatus, TaskType
from app.main import build_lifespan
from app.tasks.executor import SerialTaskExecutor


def test_startup_recovery_marks_all_inflight_tasks_restart_interrupted(
    db_session: Session,
    settings: Settings,
) -> None:
    session_factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    now = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
    with session_factory() as session:
        for status in (TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.CANCELLING):
            session.add(
                Task(
                    id=f"resource-{status.value}",
                    type=TaskType.ANALYSIS,
                    status=status,
                    client_id_hash="client",
                    ip_hash="ip",
                    input_path=str(settings.data_root / "source.fits"),
                    selected_hdu=1,
                    quota_charged=True,
                    created_at=now,
                )
            )
        session.commit()

    class InertExecutor(SerialTaskExecutor):
        def start(self) -> None:
            pass

        async def stop(self) -> None:
            self.worker_task = None

    executor = InertExecutor(session_factory, settings, clock=lambda: now)
    test_app = FastAPI(
        lifespan=build_lifespan(
            session_factory=session_factory,
            settings=settings,
            executor_factory=lambda: executor,
        )
    )
    with TestClient(test_app):
        pass

    with session_factory() as session:
        tasks = list(session.query(Task).order_by(Task.id))
        assert {task.status for task in tasks} == {TaskStatus.FAILED}
        assert {task.error_code for task in tasks} == {"restart_interrupted"}
        assert all(task.retryable for task in tasks)
