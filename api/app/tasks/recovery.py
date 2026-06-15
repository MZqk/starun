from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import EventLevel, Task, TaskStatus
from app.tasks.events import TaskEventService


def recover_interrupted_tasks(
    session_factory: sessionmaker[Session],
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> int:
    now = clock()
    events = TaskEventService(session_factory, clock=clock)
    with session_factory() as session:
        try:
            tasks = list(
                session.scalars(
                    select(Task).where(
                        or_(
                            Task.cleanup_pending.is_(True),
                            Task.status.in_(
                                [
                                    TaskStatus.QUEUED,
                                    TaskStatus.RUNNING,
                                    TaskStatus.CANCELLING,
                                ]
                            ),
                        )
                    )
                )
            )
            for task in tasks:
                if task.cleanup_pending and task.error_code == "task_expired":
                    continue
                if task.cleanup_pending or task.delete_requested_at is not None:
                    task.delete_requested_at = task.delete_requested_at or now
                    task.cancel_requested_at = task.cancel_requested_at or now
                    task.cleanup_pending = True
                    task.status = TaskStatus.CANCELLING
                    task.stage = "cancelling"
                    task.error_code = "user_deleted"
                    task.error_message = None
                    task.retryable = False
                    continue
                task.status = TaskStatus.FAILED
                task.stage = "failed"
                task.error_code = "restart_interrupted"
                task.error_message = "Task execution was interrupted by an application restart."
                task.retryable = True
                task.finished_at = now
                task.expires_at = now + timedelta(hours=24)
                events.append_in_session(
                    session,
                    task.id,
                    EventLevel.ERROR,
                    "restart_interrupted",
                    {"error_code": "restart_interrupted"},
                    created_at=now,
                )
            session.commit()
            return len(tasks)
        except BaseException:
            session.rollback()
            raise
