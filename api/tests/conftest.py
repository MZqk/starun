from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.base import Base
import app.db.models  # noqa: F401
from app.db.session import create_engine_and_session, get_db_session
from app.main import app


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
def client(db_session: Session) -> Generator[TestClient, None, None]:
    def override_db_session() -> Generator[Session, None, None]:
        yield db_session

    previous_override = app.dependency_overrides.get(get_db_session)
    app.dependency_overrides[get_db_session] = override_db_session
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        if previous_override is None:
            app.dependency_overrides.pop(get_db_session, None)
        else:
            app.dependency_overrides[get_db_session] = previous_override


@pytest.fixture
def headers() -> dict[str, str]:
    return {"X-Starun-Client-Id": "test-client"}
