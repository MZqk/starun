from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="STARUN_")

    database_url: str = "sqlite:///./starun.db"
    data_root: Path = Path("./data")
    max_upload_bytes: int = 500 * 1024 * 1024
    upload_ttl_seconds: int = 3600
    task_ttl_seconds: int = 86400
    daily_task_limit: int = 5
    analysis_timeout_seconds: int = 600
    processing_timeout_seconds: int = 3600
    min_free_disk_bytes: int = 5 * 1024 * 1024 * 1024
