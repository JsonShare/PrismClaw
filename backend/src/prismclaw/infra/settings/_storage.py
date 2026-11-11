from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class StorageSettings(BaseSettings):
    postgres_dsn: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/prismclaw"

    model_config = SettingsConfigDict(env_prefix="PRISMCLAW_STORAGE_")
