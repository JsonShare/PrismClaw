from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AffinitySettings(BaseSettings):
    enabled: bool = False
    ttl_seconds: int = 300
    heartbeat_interval: int = 30
    stale_threshold: int = 90
    forward_prefix: str = "prismclaw:forward:"
    renewal_interval: int = 60

    model_config = SettingsConfigDict(
        env_prefix="PRISMCLAW_AFFINITY_",
        populate_by_name=True,
        extra="ignore",
    )


class SandboxFilesystemConfig(BaseModel):
    allow_write: list[str] = Field(default_factory=list)
    deny_read: list[str] = Field(default_factory=list)
    deny_write: list[str] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class SandboxNetworkConfig(BaseModel):
    allowed_domains: list[str] = Field(default_factory=list)
    denied_domains: list[str] = Field(
        default_factory=lambda: ["169.254.169.254"],
    )

    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class SandboxSettings(BaseSettings):
    enforce: bool = Field(True, description="沙箱强制执行，生产环境恒为 True")
    default_filesystem: SandboxFilesystemConfig = Field(
        default_factory=lambda: SandboxFilesystemConfig(),
    )
    default_network: SandboxNetworkConfig = Field(
        default_factory=lambda: SandboxNetworkConfig(),
    )
    default_env_allowlist: list[str] = Field(
        default_factory=list
    )

    @field_validator("default_env_allowlist")
    @classmethod
    def _reject_glob_in_default_allowlist(cls, value: list[str]) -> list[str]:
        from prismclaw.schema.security.deny_names import validate_env_allowlist

        validate_env_allowlist(value)
        return value

    model_config = SettingsConfigDict(
        env_prefix="PRISMCLAW_SANDBOX_",
        populate_by_name=True,
        extra="ignore",
    )
