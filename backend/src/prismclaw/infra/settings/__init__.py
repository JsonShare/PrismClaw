"""PrismClaw settings — composite configuration with per-subsystem sub-Settings.

All public names are re-exported here so that external code can continue to use::

    from prismclaw.infra.settings import Settings, RedisSettings, load_settings, …

This package replaces the former single-file ``infra/settings.py``.
Import paths remain identical; no downstream changes are needed.
"""

from __future__ import annotations

from ._agent import AgentSettings, ServerSettings
from ._database import DatabaseSettings
from ._dream import DreamSettings, EvolutionSettings
from ._helpers import config_file_owner, find_config_file, load_settings, persist_plugin_trust
from ._memory import CuratorSettings, MemorySettings
from ._models import (
    EmbeddingSettings,
    ModelsSettings,
    ProviderSettings,
    SpeechSettings,
    VisionSettings,
)
from ._plugins import PluginSettings, SchedulerSettings, SubAgentSettings
from ._redis import RedisSettings
from ._sandbox import (
    AffinitySettings,
    SandboxFilesystemConfig,
    SandboxNetworkConfig,
    SandboxSettings,
)
from ._settings import Settings
from ._storage import StorageSettings
from ._web import ChannelsSettings, SmtpSettings, WebSettings, WebUserConfig, WebUserStoreSettings
from ._workspace import BuiltinToolSettings, SkillSettings, WorkspaceSettings

__all__ = [
    # Main entry
    "Settings",
    "load_settings",
    "find_config_file",
    "persist_plugin_trust",
    "config_file_owner",
    # Infrastructure
    "RedisSettings",
    "DatabaseSettings",
    "StorageSettings",
    # Memory & evolution
    "MemorySettings",
    "CuratorSettings",
    "DreamSettings",
    "EvolutionSettings",
    # Models
    "EmbeddingSettings",
    "VisionSettings",
    "SpeechSettings",
    "ProviderSettings",
    "ModelsSettings",
    # Agent & server
    "AgentSettings",
    "ServerSettings",
    # Web / channels
    "WebUserConfig",
    "WebUserStoreSettings",
    "WebSettings",
    "SmtpSettings",
    "ChannelsSettings",
    # Workspace & skills
    "WorkspaceSettings",
    "SkillSettings",
    "BuiltinToolSettings",
    # Sandbox
    "AffinitySettings",
    "SandboxFilesystemConfig",
    "SandboxNetworkConfig",
    "SandboxSettings",
    # Plugins & scheduling
    "PluginSettings",
    "SubAgentSettings",
    "SchedulerSettings",
]
