"""插件描述符。

- ``source_priority`` 通过 ``plugin_id.priority_rank`` 属性委托
- ``PluginDescriptor`` 直接持有 ``plugin_id`` 作为身份
- v5 渐进式披露：``SkillDescriptor`` 持有 ``pipeline_context`` 和 ``marketplace_name``
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from prismclaw.schema.agent.capability import (
    PluginCapabilityGraph,
    PluginCommandCapability,
    RuntimeCapabilities,
)
from prismclaw.engine.plugins.registry.identity import PluginID
from prismclaw.engine.plugins.lifecycle.state import PluginLifecycleState

if TYPE_CHECKING:
    # Skill/Agent manifest 类型只用于 descriptor 字段注解,不做运行时引用
    from prismclaw.engine.subagent.definition.manifest import AgentManifest
    from prismclaw.schema.services.skills import SkillManifest

DescriptorKind = Literal["plugin", "skill", "command", "agent", "hook"]


@dataclass(slots=True)
class BaseDescriptor:
    kind: DescriptorKind = field(init=False)
    canonical_id: str
    plugin_name: str | None = None
    origin: str | None = None


@dataclass(slots=True)
class PluginDescriptor(BaseDescriptor):
    """插件描述符。v4：以 PluginID 为身份，无 legacy_payload。"""

    kind: Literal["plugin"] = field(default="plugin", init=False)
    plugin_name: str = ""
    namespace: str = ""
    root_path: Path | None = None
    capability_graph: PluginCapabilityGraph | None = None
    runtime_capabilities: RuntimeCapabilities | None = None
    resolved_user_config: dict[str, Any] = field(default_factory=dict)
    hooks: list[Any] = field(default_factory=list)
    hooks_config: dict[str, Any] = field(default_factory=dict)
    hook_handlers: dict[str, list[Any]] = field(default_factory=dict)
    mcp_servers: dict[str, Any] = field(default_factory=dict)
    output_styles: dict[str, str] = field(default_factory=dict)
    # 插件级指令：来自 PRISMCLAW.md（子插件级 + marketplace 级继承）
    plugin_instructions: str | None = None
    # 身份与生命周期（v4 核心字段）
    plugin_id: PluginID | None = None
    trust_tier: str = "untrusted"
    lifecycle_state: PluginLifecycleState = PluginLifecycleState.UNINSTALLED
    explicitly_configured: bool = False
    # v5 渐进式披露：所属 marketplace 名称
    marketplace_name: str | None = None
    # v5 渐进式披露：marketplace / 插件级描述（来自市场级 plugin.json 的 description）
    marketplace_description: str | None = None
    # 子插件级描述（来自子插件自身 manifest description），与包级描述分层
    plugin_description: str | None = None

    @property
    def source_priority(self) -> int:
        """来源优先级（从 plugin_id 派生）。session > marketplace > local > builtin。"""
        if self.plugin_id is None:
            return 0
        return self.plugin_id.priority_rank


@dataclass(slots=True)
class SkillDescriptor(BaseDescriptor):
    kind: Literal["skill"] = field(default="skill", init=False)
    manifest: "SkillManifest | None" = None
    # v5 渐进式披露:延迟 content pipeline 参数
    # 从 SkillManifest.pipeline_context 复制，供 body 按需加载时使用。
    pipeline_context: dict[str, Any] = field(default_factory=dict)
    # v5 渐进式披露：所属 marketplace 名称（如 medical-healthcare）
    # 用于系统提示词按包名聚合展示插件技能。
    marketplace_name: str | None = None
    # v5 渐进式披露：marketplace / 插件级描述（来自市场级 plugin.json 的 description）
    marketplace_description: str | None = None
    # 子插件级描述（来自子插件自身 manifest description），与包级描述分层
    plugin_description: str | None = None


@dataclass(slots=True)
class CommandDescriptor(BaseDescriptor):
    kind: Literal["command"] = field(default="command", init=False)
    capability: PluginCommandCapability | None = None
    # v5 渐进式披露：所属 marketplace 名称与描述
    marketplace_name: str | None = None
    marketplace_description: str | None = None
    # 子插件级描述（来自子插件自身 manifest description）
    plugin_description: str | None = None


@dataclass(slots=True)
class AgentDescriptor(BaseDescriptor):
    kind: Literal["agent"] = field(default="agent", init=False)
    manifest: "AgentManifest | None" = None
    # v5 渐进式披露：所属 marketplace 名称与描述
    marketplace_name: str | None = None
    marketplace_description: str | None = None
    # 子插件级描述（来自子插件自身 manifest description）
    plugin_description: str | None = None


@dataclass(slots=True)
class HookDescriptor(BaseDescriptor):
    kind: Literal["hook"] = field(default="hook", init=False)
    event_name: str | None = None
    handler: Any = None
    source_kind: Literal["native", "declarative"] = "native"
