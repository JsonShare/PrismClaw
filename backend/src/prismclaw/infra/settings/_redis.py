from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RedisSettings(BaseSettings):
    host: str = "localhost"
    port: int = 6379
    database: int = 0
    password: str | None = None
    url: str = ""
    key_prefix: str = Field("prismclaw:")
    transcript_retention_days: int = Field(365)

    model_config = SettingsConfigDict(
        env_prefix="PRISMCLAW_REDIS_",
        populate_by_name=True,
    )

    @property
    def ttl_seconds(self) -> int:
        return self.transcript_retention_days * 86_400

    def build_url(self) -> str:
        if self.url:
            return self.url
        if self.password:
            import urllib.parse
            # URL-encode the password to handle special characters like '#'
            encoded_password = urllib.parse.quote(self.password)
            return f"redis://:{encoded_password}@{self.host}:{self.port}/{self.database}"
        return f"redis://{self.host}:{self.port}/{self.database}"
