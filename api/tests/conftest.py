from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.base import Base
from app.db import models as _models  # noqa: F401
from app.db.session import create_engine_and_session, get_db_session
from app.main import app, build_lifespan
from app.security.rate_limit import reset_rate_limiters
from app.tasks.executor import SerialTaskExecutor


@pytest.fixture(autouse=True)
def isolated_rate_limiters() -> Generator[None, None, None]:
    reset_rate_limiters()
    yield
    reset_rate_limiters()


@pytest.fixture
def data_root(tmp_path: Path) -> Path:
    path = tmp_path / "data"
    path.mkdir()
    return path


@pytest.fixture
def settings(tmp_path: Path, data_root: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        data_root=data_root,
        max_upload_bytes=1024 * 1024,
        min_free_disk_bytes=0,
    )


@pytest.fixture
def db_session(settings: Settings) -> Generator[Session, None, None]:
    engine, session_factory = create_engine_and_session(settings.database_url)
    Base.metadata.create_all(engine)
    try:
        with session_factory() as session:
            yield session
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def client(settings: Settings, db_session: Session) -> Generator[TestClient, None, None]:
    from app.uploads.service import get_settings

    session_factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)

    class InertTaskExecutor(SerialTaskExecutor):
        def start(self) -> None:
            pass

        async def stop(self) -> None:
            self.worker_task = None

    def override_db_session() -> Generator[Session, None, None]:
        yield db_session

    previous_lifespan = app.router.lifespan_context
    previous_db_override = app.dependency_overrides.get(get_db_session)
    previous_settings_override = app.dependency_overrides.get(get_settings)
    app.router.lifespan_context = build_lifespan(
        executor_factory=lambda: InertTaskExecutor(session_factory, settings),
    )
    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.router.lifespan_context = previous_lifespan
        if previous_db_override is None:
            app.dependency_overrides.pop(get_db_session, None)
        else:
            app.dependency_overrides[get_db_session] = previous_db_override
        if previous_settings_override is None:
            app.dependency_overrides.pop(get_settings, None)
        else:
            app.dependency_overrides[get_settings] = previous_settings_override


@pytest.fixture
def headers() -> dict[str, str]:
    return {"X-Starun-Client-Id": "test-client"}
