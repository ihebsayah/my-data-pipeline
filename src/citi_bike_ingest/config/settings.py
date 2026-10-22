from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "citi_bike"
    postgres_password: str = "citi_bike_dev"
    postgres_db: str = "citi_bike"

    data_dir: str = "./data"
    cache_dir: str = "./data/cache"
    log_level: str = "INFO"
    default_month: str = "202601"

    batch_size: int = 50000
    retry_attempts: int = 3
    retry_delay: float = 2.0

    @property
    def database_url(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


def get_settings() -> Settings:
    return Settings()
