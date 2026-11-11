from __future__ import annotations

from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class PluginSettings(BaseSettings):
    # 静态发现链：repo 管控、sync 到 assets/，绝对 home 路径。
    # 插件安装/运行时数据固定落在用户级 plugins-data/（引擎自有安装存储）。
    asset_plugins_dir: str = Field("~/.prismclaw/assets/plugins")

    # Store specific configurations for individual plugins (e.g., API keys, feature flags)
    # The key is the plugin namespace (e.g., 'commercial-legal'), and the value is a dict of options.
    plugin_configs: dict[str, dict[str, Any]] = Field(
        default_factory=dict
    )

    # 配置驱动发现。对标参考实现 ``settings.enabledPlugins``。
    # 显式启用的插件 ID 列表（name@source 格式）。
    enabled: list[str] = Field(default_factory=list)

    # 企业策略锁定的插件名。对标参考实现 ``managedPluginNames``。
    managed_names: set[str] = Field(default_factory=set)

    # 被策略封锁的来源。对标参考实现 ``blockedMarketplaces``。
    blocked_sources: set[str] = Field(default_factory=set)

    # 白名单来源（非空时只允许白名单内的来源）。对标参考实现 ``strictKnownMarketplaces``。
    strict_known_sources: set[str] | None = Field(
        default=None
    )

    # 显式信任的插件 ID（name@source）。受信任插件即使在不受信任工作区也可加载
    # （绕过 local/session 来源的工作区信任门控）。由 ``/plugins trust <id>`` 写入。
    trusted_plugins: list[str] = Field(default_factory=list)

    model_config = SettingsConfigDict(populate_by_name=True, extra="ignore")


class SubAgentSettings(BaseSettings):
    enabled: bool = True
    max_subagent_depth: int = 3

    # 静态发现链：repo 管控、sync 到 assets/，绝对 home 路径。
    asset_subagents_dir: str = Field("~/.prismclaw/assets/agents")

    model_routing: dict[str, str] = Field(
        default_factory=dict
    )
    default_max_turns: int = Field(30)
    default_timeout_seconds: float = Field(300.0)
    max_depth: int = Field(1)
    max_parallel_per_turn: int = Field(
        5, ge=1, le=20
    )
    max_handoff_chain_length: int = Field(
        5, ge=1, le=20
    )
    handoff_enabled: bool = Field(True)
    pending_handoff_ttl_turns: int = Field(
        3, ge=1, le=10
    )
    handoff_reverse_lookup_enabled: bool = Field(
        True
    )
    handoff_phase3_deploy_grace_window_s: int = Field(
        1800, ge=0, le=86400
    )

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
