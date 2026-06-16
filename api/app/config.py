from enum import StrEnum
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentProtocol(StrEnum):
    RESPONSES = "responses"
    CHAT_COMPLETIONS = "chat_completions"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="STARUN_",
        env_file=(".env", "api/.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "sqlite:///./starun.db"
    data_root: Path = Path("./data")
    max_upload_bytes: int = 500 * 1024 * 1024
    upload_ttl_seconds: int = 3600
    task_ttl_seconds: int = 86400
    daily_task_limit: int = 5
    analysis_timeout_seconds: int = 600
    processing_timeout_seconds: int = 3600
    min_free_disk_bytes: int = 5 * 1024 * 1024 * 1024
    ai_base_url: str = "https://api.moonshot.cn/v1"
    ai_api_key: SecretStr | None = None
    ai_model: str = "kimi-k2.6"
    ai_timeout_seconds: float = Field(default=180, gt=0, le=600)
    image_ai_base_url: str = "https://tokenhub.tencentmaas.com/v1"
    image_ai_api_key: SecretStr | None = None
    image_ai_model: str = "hy-image-v3.0"
    image_ai_timeout_seconds: float = Field(default=300, gt=0, le=900)
    image_ai_max_response_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    image_ai_max_edge: int = Field(default=1024, ge=512, le=4096)
    image_ai_allowed_download_hosts: str = (
        "tokenhub.tencentmaas.com,aiart-1258344699.cos.ap-guangzhou.myqcloud.com"
    )
    web_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    @property
    def allowed_image_download_hosts(self) -> frozenset[str]:
        return frozenset(
            host.strip().lower()
            for host in self.image_ai_allowed_download_hosts.split(",")
            if host.strip()
        )

    @property
    def allowed_web_origins(self) -> list[str]:
        return [
            origin.strip().rstrip("/")
            for origin in self.web_origins.split(",")
            if origin.strip()
        ]
