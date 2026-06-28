import logging

from app.main import configure_logging


def test_configure_logging_follows_uvicorn_debug(monkeypatch) -> None:
    monkeypatch.delenv("STARUN_LOG_LEVEL", raising=False)
    uvicorn_logger = logging.getLogger("uvicorn.error")
    original_uvicorn_level = uvicorn_logger.level
    try:
        uvicorn_logger.setLevel(logging.DEBUG)

        level = configure_logging()

        assert level == logging.DEBUG
        assert logging.getLogger("openai._base_client").level == logging.DEBUG
        assert logging.getLogger("httpcore").level == logging.DEBUG
        assert logging.getLogger("openai.agents").level == logging.DEBUG
    finally:
        uvicorn_logger.setLevel(original_uvicorn_level)
