"""PluginLifecycleManager：构建 runner-scoped 插件运行时 bundle。

- 异步 ``get_bundle()`` / ``rebuild()``
- ``_build_extension_manager`` 创建空 ``ExtensionManager``，由 ``load_from_load_result`` 填充
- ``get_bundle`` 走新流水线：``assemble_plugin_load_result`` → ``load_from_load_result``
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from prismclaw.infra.paths import build_project_capability_layout
from prismclaw.infra.settings import Settings
from prismclaw.infra.task_manager import TaskManager
from prismclaw.schema.shared import CapabilityKind, ProjectCapabilityLayout

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PluginRuntimeBundle:
    extension_manager: Any
    skill_provider: Any
    hook_dispatcher: Any
    runtime_mcp_manager: Any = None
    capability_layout: ProjectCapabilityLayout | None = None


class PluginLifecycleManager:
    """构建 runner-scoped 插件运行时 bundle。

    v4：全面异步化，删除同步 ``get_bundle()``。
    """

    def __init__(
        self,
        settings: Settings,
        *,
        workspace_default: str | Path,
        project_root: Path | str | None,
        additional_project_roots: list[Path | str] | None = None,
        task_manager: TaskManager | None = None,
        base_mcp_manager: Any = None,
        capability_layout: ProjectCapabilityLayout | None = None,
    ) -> None:
        self._settings = settings
        self._workspace_default = workspace_default
        self._project_root = project_root
        self._additional_project_roots = additional_project_roots or []
        self._task_manager = task_manager
        self._base_mcp_manager = base_mcp_manager
        # 由 deps 边界注入的唯一能力布局；None 时在首次需要时经 builder 现场构造。
        self._injected_layout = capability_layout
        self._cached_layout: ProjectCapabilityLayout | None = None
        self._bundle: PluginRuntimeBundle | None = None
        self._generation = 0

    def _capability_layout(self) -> ProjectCapabilityLayout:
        """返回本 manager 使用的唯一项目能力布局（注入优先，否则缓存现场构造）。"""
        if self._injected_layout is not None:
            return self._injected_layout
        if self._cached_layout is None:
            self._cached_layout = build_project_capability_layout(
                primary=self._project_root,
                additional=self._additional_project_roots,
                skills_dir=self._settings.skills.workspace_skills_dir,
                extra_skills_dirs=(self._settings.skills.asset_skills_dir,),
                plugins_dir=self._settings.plugins.asset_plugins_dir,
                agents_dir=self._settings.subagent.asset_subagents_dir,
            )
        return self._cached_layout

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def settings(self) -> Settings:
        return self._settings

    @property
    def workspace_default_path(self) -> Path:
        return Path(self._workspace_default).expanduser()

    @property
    def capability_layout(self) -> ProjectCapabilityLayout:
        """本 manager 使用的唯一项目能力布局（含诊断），供 deps/health 复用。"""
        return self._capability_layout()

    async def get_bundle(self) -> PluginRuntimeBundle:
        """异步构建 bundle。v4：唯一入口。"""
        if self._bundle is None:
            self._bundle = await self._build_bundle()
        return self._bundle

    async def rebuild(self) -> PluginRuntimeBundle:
        """强制重建 bundle。v4：async。"""
        self._generation += 1
        self._bundle = await self._build_bundle()
        return self._bundle

    def invalidate(self) -> None:
        self._generation += 1
        self._bundle = None

    def update_settings(self, settings: Settings, *, invalidate: bool = True) -> None:
        self._settings = settings
        # 目录契约来自 settings，settings 变更后缓存布局需重建（注入布局不受影响）。
        self._cached_layout = None
        if invalidate:
            self.invalidate()

    def _build_extension_manager(self) -> Any:
        """v4：创建空 ExtensionManager（无 loader 注册）。"""
        from prismclaw.engine.execution.capabilities.extension_manager import ExtensionManager

        return ExtensionManager()

    def _build_hook_dispatcher(self, extension_manager: Any) -> Any:
        from prismclaw.engine.hooks import HookDispatcher

        hook_dispatcher = HookDispatcher()
        hook_dispatcher.register_extension_hooks(extension_manager)
        return hook_dispatcher

    def _build_runtime_mcp_manager(self, extension_manager: Any) -> Any:
        from prismclaw.engine.integrations.mcp.client_manager import (
            CompositeMCPManager,
            DeclarativeMCPManager,
            MCPClientManager,
            ServerStatus,
        )
        from prismclaw.engine.integrations.mcp.settings import McpServerConfig, McpSettings

        plugin_mcp_managers: list[MCPClientManager] = []
        declarative_plugin_mcp_statuses: list[ServerStatus] = []
        if self._task_manager is not None:
            plugin_views = extension_manager.iter_declarative_plugin_runtime_views()

            for plugin in plugin_views:
                plugin_name = plugin.plugin_name
                raw_servers = plugin.mcp_servers
                if not raw_servers:
                    continue

                plugin_servers: dict[str, McpServerConfig] = {}
                for server_name, config in raw_servers.items():
                    if not isinstance(server_name, str):
                        continue
                    if not isinstance(config, McpServerConfig):
                        try:
                            config = McpServerConfig.model_validate(config)
                        except Exception as exc:
                            declarative_plugin_mcp_statuses.append(
                                ServerStatus(
                                    name=f"{plugin_name}.{server_name}",
                                    status="disabled",
                                    reason=f"invalid plugin MCP schema: {exc}",
                                )
                            )
                            continue
                    if config.transport in {"http", "streamable-http"}:
                        declarative_plugin_mcp_statuses.append(
                            ServerStatus(
                                name=f"{plugin_name}.{server_name}",
                                status="disabled",
                                reason=(
                                    f"transport={config.transport} is parsed and exposed declaratively, "
                                    "but auto-connect is disabled until HTTP MCP lifecycle handling is stabilized"
                                ),
                            )
                        )
                        logger.info(
                            "Plugin MCP server %s.%s uses transport=%s; keeping declarative metadata only until HTTP MCP lifecycle is fully stabilized.",
                            plugin_name,
                            server_name,
                            config.transport,
                        )
                        continue
                    plugin_servers[f"{plugin_name}.{server_name}"] = config

                if not plugin_servers:
                    continue

                plugin_manager = MCPClientManager(
                    McpSettings(enabled=True, servers=plugin_servers),
                    task_manager=self._task_manager,
                )
                sandbox_policy = None
                if self._base_mcp_manager is not None:
                    sandbox_policy = getattr(self._base_mcp_manager, "sandbox_policy", None)
                if sandbox_policy is None:
                    from prismclaw.engine.security.sandbox.state import resolve_sandbox_state
                    from prismclaw.schema.config.config import SandboxConfig
                    try:
                        sandbox_policy = resolve_sandbox_state(SandboxConfig(enforce=True)).policy
                    except Exception:
                        sandbox_policy = resolve_sandbox_state(SandboxConfig(enforce=False)).policy
                plugin_manager.set_sandbox_policy(sandbox_policy)
                plugin_manager.start_background()
                plugin_mcp_managers.append(plugin_manager)

        composite_managers = (
            ([self._base_mcp_manager] if self._base_mcp_manager is not None else [])
            + plugin_mcp_managers
        )
        if declarative_plugin_mcp_statuses:
            composite_managers.append(DeclarativeMCPManager(declarative_plugin_mcp_statuses))
        return CompositeMCPManager(composite_managers) if composite_managers else None

    async def _build_bundle(self) -> PluginRuntimeBundle:
        """v4：唯一 bundle 构建路径，全面异步化。"""
        from prismclaw.engine.plugins.loading.discovery_engine import PluginDirectorySource
        from prismclaw.engine.plugins.registry.identity import SESSION_SOURCE
        from prismclaw.runtime.skills.provider import DefaultSkillProvider

        # 唯一能力布局：插件目录与技能 provider 共享同一次展开结果。
        layout = self._capability_layout()

        dir_sources: list[PluginDirectorySource] = []
        for directory in layout.existing_locations_of(CapabilityKind.PLUGINS):
            dir_sources.append(
                PluginDirectorySource(path=directory, source=SESSION_SOURCE, priority=2)
            )

        extension_manager = self._build_extension_manager()
        from prismclaw.assembly.plugins.load_wiring import assemble_plugin_load_result
        from prismclaw.engine.plugins.trust import resolve_workspace_trusted

        workspace_trusted = resolve_workspace_trusted(
            self._workspace_default, self._settings
        )
        load_result = await assemble_plugin_load_result(
            directory_sources=dir_sources,
            settings=self._settings,
            workspace_trusted=workspace_trusted,
        )
        extension_manager.load_from_load_result(load_result)

        return PluginRuntimeBundle(
            extension_manager=extension_manager,
            skill_provider=DefaultSkillProvider(
                settings=self._settings.skills,
                project_root=self._project_root,
                additional_project_roots=self._additional_project_roots,
                project_locations=layout.existing_locations_of(CapabilityKind.SKILLS),
            ),
            hook_dispatcher=self._build_hook_dispatcher(extension_manager),
            runtime_mcp_manager=self._build_runtime_mcp_manager(extension_manager),
            capability_layout=layout,
        )

