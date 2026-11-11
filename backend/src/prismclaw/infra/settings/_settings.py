from __future__ import annotations

import re
from typing import Any, cast

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from prismclaw.schema.config.domains import DomainSettings
from prismclaw.schema.services.mcp_settings import McpSettings

from ._agent import AgentSettings, ServerSettings
from ._database import DatabaseSettings
from ._dream import EvolutionSettings
from ._memory import MemorySettings
from ._models import (
    EmbeddingSettings,
    ModelsSettings,
    SpeechSettings,
    VisionSettings,
)
from ._plugins import PluginSettings, SchedulerSettings, SubAgentSettings
from ._redis import RedisSettings
from ._runtime import RuntimeSettings
from ._sandbox import AffinitySettings, SandboxSettings
from ._storage import StorageSettings
from ._web import ChannelsSettings
from ._workspace import BuiltinToolSettings, SkillSettings, WorkspaceSettings


def _default_mcp_settings() -> McpSettings:
    return McpSettings()


_ENV_PLACEHOLDER_RE = re.compile(r"^\{env:([A-Za-z_][A-Za-z0-9_]*)\}$")


def _resolve_env_placeholders(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _resolve_env_placeholders(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env_placeholders(item) for item in value]
    if isinstance(value, str):
        match = _ENV_PLACEHOLDER_RE.match(value.strip())
        if match:
            import os
            return os.getenv(match.group(1), "")
    return value


class Settings(BaseSettings):
    server: ServerSettings = Field(default_factory=lambda: ServerSettings())
    redis: RedisSettings = Field(default_factory=lambda: RedisSettings())
    database: DatabaseSettings = Field(default_factory=lambda: DatabaseSettings())
    storage: StorageSettings = Field(default_factory=lambda: StorageSettings())
    memory: MemorySettings = Field(default_factory=lambda: MemorySettings())
    embedding: EmbeddingSettings = Field(default_factory=lambda: EmbeddingSettings())
    vision: VisionSettings = Field(default_factory=lambda: VisionSettings())
    speech: SpeechSettings = Field(default_factory=lambda: SpeechSettings())
    agent: AgentSettings = Field(default_factory=lambda: AgentSettings())
    models: ModelsSettings = Field(default_factory=lambda: ModelsSettings())
    channels: ChannelsSettings = Field(default_factory=lambda: ChannelsSettings())
    workspaces: WorkspaceSettings = Field(default_factory=lambda: WorkspaceSettings())
    skills: SkillSettings = Field(default_factory=lambda: SkillSettings())
    evolution: EvolutionSettings = Field(default_factory=lambda: EvolutionSettings())
    affinity: AffinitySettings = Field(default_factory=lambda: AffinitySettings())
    runtime: RuntimeSettings = Field(default_factory=lambda: RuntimeSettings())
    sandbox: SandboxSettings = Field(default_factory=lambda: SandboxSettings())
    subagent: SubAgentSettings = Field(default_factory=lambda: SubAgentSettings())
    scheduler: SchedulerSettings = Field(default_factory=lambda: SchedulerSettings())
    plugins: PluginSettings = Field(default_factory=lambda: PluginSettings())
    mcp: McpSettings = Field(default_factory=_default_mcp_settings)
    builtin_tools: BuiltinToolSettings = Field(
        default_factory=lambda: BuiltinToolSettings()
    )
    domains: DomainSettings = Field(default_factory=lambda: DomainSettings())
    # Graceful shutdown timeout in seconds.  Matches the default K8s
    # SIGTERM→SIGKILL window (30 s) so that TaskManager drain completes
    # before the orchestrator force-kills the process.
    shutdown_grace_seconds: int = Field(30)

    model_config = SettingsConfigDict(env_prefix="PRISMCLAW_")

    @model_validator(mode="before")
    @classmethod
    def _normalize_input(cls, data):
        if isinstance(data, dict):
            data = {k: v for k, v in data.items() if not k.startswith("_comment")}
            data = cast(dict[str, Any], _resolve_env_placeholders(data))
        return data

    @model_validator(mode="after")
    def _apply_env_overrides(self):
        """环境变量优先于配置文件，供容器部署注入密钥。"""
        import os

        jwt_secret = os.getenv("PRISMCLAW_WEB_JWT_SECRET", "").strip()
        if jwt_secret:
            self.channels.web.jwt_secret = jwt_secret
        return self
