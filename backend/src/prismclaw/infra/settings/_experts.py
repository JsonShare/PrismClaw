from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ExpertSettings(BaseSettings):
    # 静态发现链：repo 管控、sync 到 assets/，绝对 home 路径。
    asset_experts_dir: str = Field("~/.prismclaw/assets/experts")

    model_config = SettingsConfigDict(populate_by_name=True, extra="ignore")


class SubAgentSettings(BaseSettings):
    enabled: bool = True
    max_subagent_depth: int = 3

    # 静态发现链：repo 管控、sync 到 assets/，绝对 home 路径。
    asset_subagents_dir: str = Field("~/.prismclaw/assets/agents")

    model_routing: dict[str, str] = Field(default_factory=dict)
    default_max_turns: int = Field(30)
    default_timeout_seconds: float = Field(300.0)
    max_depth: int = Field(1)
    max_parallel_per_turn: int = Field(5, ge=1, le=20)

    model_config = SettingsConfigDict(
        env_prefix="PRISMCLAW_SUBAGENT_",
        populate_by_name=True,
        extra="ignore",
    )


class SchedulerSettings(BaseSettings):
    """User-facing scheduled task automation."""

    enabled: bool = True
    dialogue_creation_enabled: bool = Field(True)
    tick_interval_seconds: float = Field(1.0)
    jitter_fraction: float = Field(0.1)
    jitter_cap_seconds: float = Field(60.0)
    max_concurrent_executions: int = Field(3)
    max_concurrent_per_user: int = Field(2)
    max_tasks_per_user: int = Field(20)
    max_consecutive_failures: int = Field(3)
    missed_policy_default: str = Field("skip")
    dispatch_key_prefix: str = Field("prismclaw:scheduler:dispatch:")
    leader_lock_key: str = Field("prismclaw:scheduler:leader")
    leader_lock_ttl_ms: int = Field(5000)

    model_config = SettingsConfigDict(
        env_prefix="PRISMCLAW_SCHEDULER_",
        populate_by_name=True,
        extra="ignore",
    )
