import sqlite3
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import EventLevel, TaskEvent

SQLITE_RETRY_DELAYS_SECONDS = (0.01, 0.025, 0.05, 0.1, 0.2)


class UnsupportedDatabaseError(RuntimeError):
    pass


def require_sqlite(
    session_factory: sessionmaker[Session],
    component: str,
) -> None:
    bind = session_factory.kw.get("bind")
    if bind is None:
        with session_factory() as session:
            bind = session.get_bind()
    dialect_name = bind.dialect.name
    if dialect_name != "sqlite":
        raise UnsupportedDatabaseError(
            f"{component} requires SQLite; configured dialect is {dialect_name!r}."
        )


class TaskEventService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        require_sqlite(session_factory, "TaskEventService")
        self._session_factory = session_factory
        self._clock = clock

    def append(
        self,
        task_id: str,
        level: EventLevel,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        created_at: datetime | None = None,
    ) -> TaskEvent:
        for attempt in range(len(SQLITE_RETRY_DELAYS_SECONDS) + 1):
            with self._session_factory() as session:
                try:
                    if session.get_bind().dialect.name == "sqlite":
                        session.execute(text("BEGIN IMMEDIATE"))
                    event = self.append_in_session(
                        session,
                        task_id,
                        level,
                        event_type,
                        payload,
                        created_at=created_at,
                    )
                    session.commit()
                    return event
                except OperationalError as exc:
                    session.rollback()
                    if not is_transient_sqlite_contention(exc) or attempt == len(
                        SQLITE_RETRY_DELAYS_SECONDS
                    ):
                        raise
            time.sleep(SQLITE_RETRY_DELAYS_SECONDS[attempt])
        raise AssertionError("unreachable")

    def append_in_session(
        self,
        session: Session,
        task_id: str,
        level: EventLevel,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        created_at: datetime | None = None,
    ) -> TaskEvent:
        sequence = (
            session.scalar(
                select(func.max(TaskEvent.sequence)).where(TaskEvent.task_id == task_id)
            )
            or 0
        ) + 1
        event = TaskEvent(
            task_id=task_id,
            sequence=sequence,
            level=level,
            event_type=event_type,
            payload=payload or {},
            created_at=(created_at or self._clock()).astimezone(UTC),
        )
        session.add(event)
        session.flush()
        return event


def is_transient_sqlite_contention(error: OperationalError) -> bool:
    original = error.orig
    if not isinstance(original, sqlite3.OperationalError):
        return False
    error_code = getattr(original, "sqlite_errorcode", None)
    primary_error_code = error_code & 0xFF if isinstance(error_code, int) else None
    if primary_error_code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
        return True
    message = str(original).lower()
    return "database is locked" in message or "database table is locked" in message
