from __future__ import annotations

from pathlib import Path
from typing import Any


async def assemble_plugin_load_result(
    *,
    install_store: Any | None = None,
    directory_sources: list[Any] | None = None,
    settings: Any | None = None,
    workspace_trusted: bool = True,
    data_base_dir: Path | None = None,
) -> Any:
    from prismclaw.engine.plugins.loading.discovery_engine import (
        PluginDiscoveryEngine,
        PluginLoadOrchestrator,
    )
    from prismclaw.engine.plugins.loading.install_store import InstalledPluginsStore
    from prismclaw.engine.plugins.loading.load_strategy import default_strategies
    from prismclaw.runtime.skills.discovery import _scan_skills_directory_frontmatter_only
    from prismclaw.assembly.plugins.agent_manifest_loader import DefaultPluginAgentLoader

    managed_names: set[str] | None = None
    blocked_sources: set[str] | None = None
    strict_known: set[str] | None = None
    enabled_ids: list[str] = []
    plugin_configs: dict[str, dict[str, Any]] = {}
    trusted_plugins: set[str] = set()

    if settings is not None:
        plugin_settings = getattr(settings, "plugins", None)
        if plugin_settings is not None:
            enabled_ids = list(getattr(plugin_settings, "enabled", []) or [])
            managed_names = set(getattr(plugin_settings, "managed_names", set()) or set())
            blocked_sources = set(getattr(plugin_settings, "blocked_sources", set()) or set())
            strict_known = getattr(plugin_settings, "strict_known_sources", None)
            plugin_configs = dict(getattr(plugin_settings, "plugin_configs", {}) or {})
        from prismclaw.engine.plugins.trust import trusted_plugins_from_settings

        trusted_plugins = trusted_plugins_from_settings(settings)

    # 安装记录属于用户级运行时状态，不能放 assets/（sync 会整目录重拷抹掉）。
    # 与 plugins-data 同级落在 home 根。
    store = install_store or InstalledPluginsStore(
        Path.home() / ".prismclaw" / "plugins-data" / "installed_plugins.json"
    )

    agent_loader = DefaultPluginAgentLoader()
    strategies = default_strategies(
        agent_loader=agent_loader,
        data_base_dir=data_base_dir,
        skill_scanner=_scan_skills_directory_frontmatter_only,
    )
    discovery = PluginDiscoveryEngine(
        install_store=store,
        enabled_plugin_ids=enabled_ids,
        managed_names=managed_names,
        blocked_sources=blocked_sources,
        strict_known_sources=strict_known,
        trusted_plugins=trusted_plugins,
        directory_sources=directory_sources or [],
        strategies=strategies,
    )
    orchestrator = PluginLoadOrchestrator(
        discovery=discovery,
        strategies=strategies,
        plugin_configs=plugin_configs,
        managed_names=managed_names,
        data_base_dir=data_base_dir,
    )
    return await orchestrator.assemble_plugin_load_result(workspace_trusted=workspace_trusted)

