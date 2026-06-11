from collections.abc import Generator
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings


def create_engine_and_session(database_url: str) -> tuple[Engine, sessionmaker[Session]]:
    is_sqlite = database_url.startswith("sqlite")
    connect_args = {"check_same_thread": False} if is_sqlite else {}
    engine = create_engine(database_url, connect_args=connect_args)
    if is_sqlite:

        @event.listens_for(engine, "connect")
        def enable_sqlite_foreign_keys(
            dbapi_connection: Any,
            _connection_record: Any,
        ) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    return engine, session_factory


_application_engine, _application_session_factory = create_engine_and_session(
    Settings().database_url
)


def configure_database(database_url: str) -> Engine:
    """Replace the application engine during startup or isolated tests."""
    global _application_engine, _application_session_factory

    engine, session_factory = create_engine_and_session(database_url)
    previous_engine = _application_engine
    _application_engine = engine
    _application_session_factory = session_factory
    previous_engine.dispose()
    return engine


def get_db_session() -> Generator[Session, None, None]:
    with _application_session_factory() as session:
        yield session
