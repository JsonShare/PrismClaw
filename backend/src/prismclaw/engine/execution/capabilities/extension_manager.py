"""ExtensionManager：插件能力注册中心。

- 所有数据存储在 ``_descriptor_registry``
- 入口：``register_loaded_plugin`` / ``load_from_load_result``
- v5 渐进式披露：skill 注册时传递 ``pipeline_context`` 和 ``marketplace_name``
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from prismclaw.engine.plugins.registry.descriptors import (
    AgentDescriptor,
    CommandDescriptor,
    HookDescriptor,
    PluginDescriptor,
    SkillDescriptor,
)
from prismclaw.engine.plugins.registry import DescriptorRegistry
from prismclaw.engine.plugins.registry.runtime_views import (
    DeclarativePluginCapabilityView,
    DeclarativePluginRuntimeView,
    HookBindingView,
    HookExtensionView,
)
from prismclaw.engine.commands.plugin.plugin_runtime import build_plugin_command_runtime
from prismclaw.schema.services.skills import SkillManifest

if TYPE_CHECKING:
    from prismclaw.engine.plugins.registry.identity import PluginID
    from prismclaw.engine.plugins.loading.load_result import LoadedPlugin, PluginLoadResult

logger = logging.getLogger(__name__)


class ExtensionManager:
    """插件能力注册中心。

    所有数据存储在 ``_descriptor_registry``。
    入口是 ``register_loaded_plugin`` / ``load_from_load_result``。
    """

    def __init__(self) -> None:
        self._descriptor_registry = DescriptorRegistry()

    # ------------------------------------------------------------------
    # 注册入口
    # ------------------------------------------------------------------

    def register_loaded_plugin(
        self,
        loaded_plugin: "LoadedPlugin",
    ) -> None:
        """注册一个已加载插件。

        直接从 LoadedPlugin 结构化字段构建 PluginDescriptor，
        通过 plugin_id.priority_rank 确定优先级。
        """
        from prismclaw.engine.plugins.registry.identity import PluginID
        from prismclaw.engine.plugins.trust import trust_tier_for_source
        from prismclaw.engine.plugins.lifecycle.state import PluginLifecycleState

        plugin_id = PluginID(name=loaded_plugin.name, source=loaded_plugin.source)
        descriptor = self._build_plugin_descriptor(loaded_plugin, plugin_id)
        if descriptor is None:
            return

        descriptor.trust_tier = trust_tier_for_source(loaded_plugin.source)
        descriptor.lifecycle_state = PluginLifecycleState.LOADED
        descriptor.explicitly_configured = True

        self._descriptor_registry.register_plugin(descriptor)
        self._register_declarative_components(descriptor)
        self._register_native_components(descriptor, loaded_plugin)

    def load_from_load_result(self, result: "PluginLoadResult") -> None:
        """从 PluginLoadResult 批量注册插件。

        对标设计方案 Phase 2：assemble_plugin_load_result 的注册阶段。
        """
        for plugin in result.enabled:
            self.register_loaded_plugin(plugin)

    # ------------------------------------------------------------------
    # Descriptor 构建
    # ------------------------------------------------------------------

    def _build_plugin_descriptor(
        self,
        loaded: "LoadedPlugin",
        plugin_id: "PluginID",
    ) -> PluginDescriptor | None:
        """从 LoadedPlugin 结构化字段直接构建 PluginDescriptor。"""
        plugin_name = loaded.name
        runtime_capabilities = loaded.runtime_capabilities

        return PluginDescriptor(
            canonical_id=plugin_id.canonical,
            plugin_name=plugin_name,
            namespace=loaded.source,
            origin="plugin",
            root_path=loaded.root_path,
            capability_graph=loaded.capability_graph,
            runtime_capabilities=runtime_capabilities,
            resolved_user_config=dict(loaded.resolved_user_config),
            hooks=list(loaded.hooks),
            hooks_config=dict(loaded.hooks_config),
            hook_handlers=dict(loaded.hook_handlers),
            mcp_servers=dict(loaded.mcp_servers),
            output_styles=dict(loaded.output_styles),
            plugin_instructions=loaded.plugin_instructions,
            plugin_id=plugin_id,
            marketplace_name=loaded.marketplace_name,
            marketplace_description=(
                loaded.marketplace_description
                or (getattr(loaded.manifest, "description", None) if loaded.manifest else None)
            ),
            # 子插件级描述：始终取自身 manifest，不与包级描述混层
            plugin_description=(
                getattr(loaded.manifest, "description", None) if loaded.manifest else None
            ),
        )

    def _register_declarative_components(self, descriptor: PluginDescriptor) -> None:
        """从 PluginDescriptor.capability_graph 注册 skills/agents/commands/hooks。"""
        graph = descriptor.capability_graph
        plugin_namespace = descriptor.plugin_name
        runtime = descriptor.runtime_capabilities
        enabled_commands = self._runtime_capability_name_set(runtime, "commands")
        enabled_skills = self._runtime_capability_name_set(runtime, "skills")
        enabled_agents = self._runtime_capability_name_set(runtime, "agents")

        skills = getattr(graph, "skill_capabilities", []) if graph is not None else []
        agents = getattr(graph, "agent_capabilities", []) if graph is not None else []
        commands = (
            list(getattr(graph, "command_capabilities", []))
            if graph is not None and isinstance(getattr(graph, "command_capabilities", None), list)
            else []
        )

        for skill in skills:
            if not isinstance(skill, SkillManifest):
                continue
            namespaced_name = self._namespaced_capability_name(plugin_namespace, skill.name)
            if enabled_skills is not None and namespaced_name not in enabled_skills:
                continue
            copied_skill = skill.model_copy(
                update={
                    "name": namespaced_name,
                    "source": "plugin",
                    "loaded_from": skill.loaded_from or plugin_namespace,
                    # v5 渐进式披露：显式深拷贝 pipeline_context，避免浅拷贝交叉污染
                    "pipeline_context": dict(skill.pipeline_context),
                }
            )
            self._descriptor_registry.register_skill(
                SkillDescriptor(
                    canonical_id=namespaced_name,
                    plugin_name=plugin_namespace,
                    origin="plugin",
                    manifest=copied_skill,
                    # v5 渐进式披露：pipeline_context 从原始 skill 对象复制
                    pipeline_context=dict(skill.pipeline_context),
                    # v5 渐进式披露：marketplace_name 从 PluginDescriptor 传递
                    marketplace_name=descriptor.marketplace_name,
                    # v5 渐进式披露：marketplace_description 从 PluginDescriptor 传递
                    marketplace_description=descriptor.marketplace_description,
                    plugin_description=descriptor.plugin_description,
                )
            )

        for agent in agents:
            if not hasattr(agent, "model_copy") or not hasattr(agent, "name"):
                continue
            namespaced_name = self._namespaced_capability_name(
                plugin_namespace, getattr(agent, "name", "")
            )
            if enabled_agents is not None and namespaced_name not in enabled_agents:
                continue
            from prismclaw.engine.subagent.definition.manifest import AgentManifest
            from prismclaw.engine.execution.skills.agent_adapter import derive_skill_agent_name

            runtime_name = derive_skill_agent_name(
                namespaced_name,
                explicit_name=f"{plugin_namespace}-{getattr(agent, 'name', '')}",
            )
            model_dump = getattr(agent, "model_dump", None)
            if callable(model_dump):
                payload = model_dump(mode="json", exclude_none=True)
            else:
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            payload["name"] = runtime_name
            copied_agent = AgentManifest.model_validate(payload)
            self._descriptor_registry.register_agent(
                AgentDescriptor(
                    canonical_id=namespaced_name,
                    plugin_name=plugin_namespace,
                    origin="plugin",
                    manifest=copied_agent,
                    # v5 渐进式披露：marketplace_name / marketplace_description 从 PluginDescriptor 传递
                    marketplace_name=descriptor.marketplace_name,
                    marketplace_description=descriptor.marketplace_description,
                    plugin_description=descriptor.plugin_description,
                )
            )

        for command in commands:
            command_name = getattr(command, "name", None)
            if not isinstance(command_name, str) or not command_name:
                continue
            if enabled_commands is not None and command_name not in enabled_commands:
                continue
            self._descriptor_registry.register_command(
                CommandDescriptor(
                    canonical_id=command_name,
                    plugin_name=plugin_namespace,
                    origin="plugin",
                    capability=command,
                    # v5 渐进式披露：marketplace_name / marketplace_description 从 PluginDescriptor 传递
                    marketplace_name=descriptor.marketplace_name,
                    marketplace_description=descriptor.marketplace_description,
                    plugin_description=descriptor.plugin_description,
                )
            )

        # 声明式 hooks：注册加载期工厂已构建的可执行 handler（修复 P3-T1 断裂——
        # 此前注册原始 config dict，被下游 isinstance(HookHandler) 门控丢弃）。
        for event_name, handlers in descriptor.hook_handlers.items():
            if not isinstance(event_name, str) or not isinstance(handlers, list):
                continue
            for handler in handlers:
                self._descriptor_registry.register_hook(
                    HookDescriptor(
                        canonical_id=f"{plugin_namespace}:{event_name}",
                        plugin_name=plugin_namespace,
                        origin="plugin",
                        event_name=event_name,
                        handler=handler,
                        source_kind="declarative",
                    )
                )

        logger.info(
            "Registered declarative components for %s: %d skills, %d agents, %d commands.",
            plugin_namespace,
            len(skills),
            len(agents),
            len(commands),
        )

    def _register_native_components(
        self, descriptor: PluginDescriptor, loaded: "LoadedPlugin"
    ) -> None:
        """注册 Python 原生插件的 hooks（来自 plugin_sdk 注册）。

        注意：native_tools 当前未被消费——SDK @tool 注册的工具由
        DomainRuntimeContext 在 assembly 层统一注册到 ToolRegistry。
        """
        if not loaded.is_native:
            return
        from prismclaw.engine.plugins.hooks.handlers import CallbackHookHandler

        plugin_namespace = descriptor.plugin_name
        for hook in loaded.native_hooks:
            event_name = getattr(hook, "event_name", None)
            func = getattr(hook, "func", None)
            if not isinstance(event_name, str) or func is None:
                continue
            # SDK 存的是裸 callable，包成 CallbackHookHandler 才能过下游
            # isinstance(HookHandler) 门控（修复 P3-T1：原生 hook 此前被静默丢弃）。
            self._descriptor_registry.register_hook(
                HookDescriptor(
                    canonical_id=f"{plugin_namespace}:{event_name}",
                    plugin_name=plugin_namespace,
                    origin="plugin",
                    event_name=event_name,
                    handler=CallbackHookHandler(func),
                    source_kind="native",
                )
            )

    @staticmethod
    def _runtime_capability_name_set(runtime: Any, attr: str) -> set[str] | None:
        names = getattr(runtime, attr, None)
        if names is None or not isinstance(names, tuple):
            return None
        return {name for name in names if isinstance(name, str) and name}

    @staticmethod
    def _namespaced_capability_name(plugin_name: str, base_name: str) -> str:
        """Build the canonical ``plugin:base`` capability id.

        Namespacing is unconditional: ``base_name`` is always the plugin-local
        name taken verbatim from a fresh SKILL.md / agent manifest parse, never
        a pre-namespaced id. Registration is therefore idempotent *structurally*
        — :meth:`DescriptorRegistry.register_skill` overwrites by ``canonical_id``
        and plugin reload builds a fresh registry — so no content-based guard is
        needed here. Sniffing ``base_name`` for an existing prefix would be both
        dead code and a latent bug (a skill literally named ``foo:bar`` under
        plugin ``foo`` would wrongly skip namespacing).
        """
        return f"{plugin_name}:{base_name}"

    # ------------------------------------------------------------------
    # 只读查询 API
    # ------------------------------------------------------------------

    @property
    def registry(self) -> DescriptorRegistry:
        return self._descriptor_registry

    def iter_plugin_descriptors(self) -> list[PluginDescriptor]:
        return list(self._descriptor_registry.plugins.values())

    def get_plugin_descriptor(self, plugin_name: str) -> PluginDescriptor | None:
        """按插件名查找（跨源，返回最高优先级）。"""
        return self._descriptor_registry.by_name(plugin_name)

    def get_plugin_descriptor_by_id(self, plugin_id: "PluginID") -> PluginDescriptor | None:
        """按 PluginID 精确查找。"""
        return self._descriptor_registry.by_id(plugin_id)

    def iter_declarative_plugin_runtime_views(self) -> list[DeclarativePluginRuntimeView]:
        """插件 manifest + MCP server 状态视图。"""
        return [
            DeclarativePluginRuntimeView.from_descriptor(descriptor)
            for descriptor in self._descriptor_registry.plugins.values()
        ]

    def iter_declarative_plugin_capability_views(self) -> list[DeclarativePluginCapabilityView]:
        """插件能力图视图。"""
        views: list[DeclarativePluginCapabilityView] = []
        for descriptor in self._descriptor_registry.plugins.values():
            view = DeclarativePluginCapabilityView.from_descriptor(descriptor)
            if view is not None:
                views.append(view)
        return views

    def iter_hook_bindings(self) -> list[HookBindingView]:
        """规范化 hook 事件注册视图。"""
        from prismclaw.engine.plugins.hooks.handlers import HookHandler

        bindings: list[HookBindingView] = []
        for descriptor in self._descriptor_registry.hooks.values():
            if not isinstance(descriptor.event_name, str):
                continue
            if descriptor.handler is None:
                continue
            if not callable(descriptor.handler) and not isinstance(descriptor.handler, HookHandler):
                continue
            bindings.append(
                HookBindingView(
                    event_name=descriptor.event_name,
                    handler=descriptor.handler,
                    plugin_name=descriptor.plugin_name,
                    source_kind=descriptor.source_kind,
                )
            )
        return bindings

    def iter_hook_extension_views(self) -> list[HookExtensionView]:
        """按插件分组的 hook 扩展视图。"""
        views: list[HookExtensionView] = []
        for descriptor in self._descriptor_registry.plugins.values():
            if descriptor.hooks or descriptor.hooks_config:
                views.append(
                    HookExtensionView(
                        plugin_name=descriptor.plugin_name,
                        hooks=list(descriptor.hooks),
                        hooks_config=dict(descriptor.hooks_config),
                    )
                )
        return views

    def iter_skill_manifests(self) -> list[SkillManifest]:
        return [
            descriptor.manifest
            for descriptor in self._descriptor_registry.skills.values()
            if isinstance(descriptor.manifest, SkillManifest)
        ]

    def iter_agent_manifests(self) -> list[Any]:
        return [
            descriptor.manifest
            for descriptor in self._descriptor_registry.agents.values()
            if descriptor.manifest is not None
        ]

    def get_skill_descriptor(self, skill_name: str) -> SkillDescriptor | None:
        return self._descriptor_registry.skills.get(skill_name)

    def iter_skill_descriptors(self) -> list[SkillDescriptor]:
        return list(self._descriptor_registry.skills.values())

    def get_agent_descriptor(self, agent_name: str) -> AgentDescriptor | None:
        return self._descriptor_registry.agents.get(agent_name)

    def _resolve_command_descriptor(self, command_name: str) -> CommandDescriptor | None:
        """解析命令名，兼容 ``/plugin:command`` 命名空间写法。

        插件文档普遍按 ``/<subplugin>:<command>`` 书写，而 commands/ 目录下的命令
        以裸名注册（部分还带 ``<短名>-`` 前缀去重）。解析顺序：

        1. 精确匹配注册名（原有行为）
        2. ``plugin`` 前缀 + 同插件内的注册名
        3. ``plugin`` 前缀 + 同插件 commands/*.md 的文件名（stem）
        4. ``plugin`` 前缀 + 同插件内以 ``-<command>`` 结尾的去重名
        """
        if not command_name:
            return None
        descriptor = self._descriptor_registry.get_command(command_name)
        if descriptor is not None or ":" not in command_name:
            return descriptor

        plugin, _, suffix = command_name.lower().partition(":")
        if not plugin or not suffix:
            return None

        candidates = [
            command
            for command in self._descriptor_registry.commands.values()
            if (command.plugin_name or "").lower() == plugin
        ]
        for command in candidates:
            if (command.canonical_id or "").lower() == suffix:
                return command
        for command in candidates:
            path = self._command_path(command)
            if path and Path(path).stem.lower() == suffix:
                return command
        # 去重名（如 litigation-cn-evidence-review）：仅在候选唯一时采用，
        # 否则宁可回退，避免把用户写错的命令静默解析到无关命令。
        suffix_matches = [
            command
            for command in candidates
            if (command.canonical_id or "").lower().endswith("-" + suffix)
        ]
        if len(suffix_matches) == 1:
            return suffix_matches[0]
        return None

    @staticmethod
    def _command_path(descriptor: CommandDescriptor) -> str | None:
        capability = descriptor.capability
        if isinstance(capability, dict):
            path = capability.get("path")
        else:
            path = getattr(capability, "path", None)
        return str(path) if path else None

    def get_plugin_command_descriptor(self, command_name: str) -> CommandDescriptor | None:
        return self._resolve_command_descriptor(command_name)

    def iter_plugin_command_descriptors(self) -> list[CommandDescriptor]:
        return list(self._descriptor_registry.commands.values())

    def iter_agent_descriptors(self) -> list[AgentDescriptor]:
        return list(self._descriptor_registry.agents.values())

    def iter_hook_descriptors(self) -> list[HookDescriptor]:
        return list(self._descriptor_registry.hooks.values())

    def get_plugin_command_runtime(self, command_name: str) -> Any | None:
        descriptor = self._resolve_command_descriptor(command_name)
        if descriptor is not None and descriptor.capability is not None and descriptor.plugin_name:
            return build_plugin_command_runtime(descriptor.plugin_name, descriptor.capability)
        return None

    def get_plugin_capability_graph(self, plugin_name: str) -> Any | None:
        descriptor = self._descriptor_registry.by_name(plugin_name)
        return descriptor.capability_graph if descriptor is not None else None

    def iter_plugin_capability_graphs(self) -> list[Any]:
        return [
            view.capability_graph for view in self.iter_declarative_plugin_capability_views()
        ]

    def get_plugin_runtime_capabilities(self, plugin_name: str) -> Any | None:
        descriptor = self._descriptor_registry.by_name(plugin_name)
        return descriptor.runtime_capabilities if descriptor is not None else None

    def iter_plugin_runtime_capabilities(self) -> list[Any]:
        return [
            descriptor.runtime_capabilities
            for descriptor in self._descriptor_registry.plugins.values()
            if descriptor.runtime_capabilities is not None
        ]


_global_extension_manager: ExtensionManager | None = None


def get_extension_manager() -> ExtensionManager:
    """全局 ExtensionManager 单例。"""
    global _global_extension_manager
    if _global_extension_manager is None:
        _global_extension_manager = ExtensionManager()
    return _global_extension_manager
