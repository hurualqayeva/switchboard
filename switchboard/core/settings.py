"""Application settings, loaded from environment variables and an optional .env file."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SWITCHBOARD_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: Literal["local", "test", "aws"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_json: bool = Field(default=False, description="Emit JSON logs (always on outside local)")

    host: str = "0.0.0.0"  # noqa: S104 - the service runs in a container
    port: int = 8000

    database_url: PostgresDsn = Field(
        default=PostgresDsn("postgresql://switchboard:switchboard@localhost:5432/switchboard"),
    )
    redis_url: RedisDsn = Field(default=RedisDsn("redis://localhost:6379/0"))

    catalog_path: str = "catalog/models.yaml"

    storage: Literal["postgres", "memory"] = "postgres"
    auto_migrate: bool = Field(default=True, description="Apply pending SQL migrations at startup")
    ledger_flush_interval_s: float = Field(default=1.0, gt=0)

    @property
    def is_local(self) -> bool:
        return self.env == "local"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
