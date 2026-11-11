"""插件注册表。

v4 重构：
- ``plugins`` 以 ``PluginID`` 字符串（``name@source``）为 key
- 删除 ``get_plugin(name)``，新增 ``by_id(plugin_id)`` 和 ``by_name(name)``（多匹配）
- ``by_state`` 等查询方法基于 ``PluginID``
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from prismclaw.engine.plugins.registry.descriptors import (
    AgentDescriptor,
    CommandDescriptor,
    HookDescriptor,
    PluginDescriptor,
    SkillDescriptor,
)

if TYPE_CHECKING:
    from prismclaw.engine.plugins.registry.identity import PluginID
    from prismclaw.engine.plugins.lifecycle.state import PluginLifecycleState


@dataclass(slots=True)
class DescriptorRegistry:
    """插件能力注册表。

    plugins 以 ``PluginID.canonical`` (``name@source``) 为 key。
    by_name 用于跨源查找（返回最高优先级匹配）。
    """

    plugins: dict[str, PluginDescriptor] = field(default_factory=dict)
    skills: dict[str, SkillDescriptor] = field(default_factory=dict)
    commands: dict[str, CommandDescriptor] = field(default_factory=dict)
    agents: dict[str, AgentDescriptor] = field(default_factory=dict)
    hooks: dict[str, HookDescriptor] = field(default_factory=dict)

    def clear(self) -> None:
        self.plugins.clear()
        self.skills.clear()
        self.commands.clear()
        self.agents.clear()
        self.hooks.clear()

    def register_plugin(self, descriptor: PluginDescriptor) -> None:
        """以 PluginID.canonical 注册。"""
        key = descriptor.plugin_id.canonical if descriptor.plugin_id else descriptor.canonical_id
        self.plugins[key] = descriptor

    def register_skill(self, descriptor: SkillDescriptor) -> None:
        self.skills[descriptor.canonical_id] = descriptor

    def register_command(self, descriptor: CommandDescriptor) -> None:
        self.commands[descriptor.canonical_id.lower()] = descriptor

    def register_agent(self, descriptor: AgentDescriptor) -> None:
        self.agents[descriptor.canonical_id] = descriptor

    def register_hook(self, descriptor: HookDescriptor) -> None:
        key = f"{descriptor.plugin_name or 'global'}:{descriptor.event_name or 'config'}:{len(self.hooks)}"
        self.hooks[key] = descriptor

    def by_id(self, plugin_id: "PluginID") -> PluginDescriptor | None:
        """按 PluginID 精确查找。"""
        return self.plugins.get(plugin_id.canonical)

    def by_name(self, name: str) -> PluginDescriptor | None:
        """按插件名查找，返回最高优先级匹配。

        对标设计方案：session > marketplace > local > builtin。
        """
        candidates = [
            desc for desc in self.plugins.values()
            if desc.plugin_name == name
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda d: d.source_priority)

    def get_command(self, command_name: str) -> CommandDescriptor | None:
        return self.commands.get(command_name.lower())

    def by_state(self, state: "PluginLifecycleState") -> list[PluginDescriptor]:
        """按生命周期状态过滤插件。对标设计方案 Phase 3。"""
        return [
            desc for desc in self.plugins.values() if desc.lifecycle_state == state
        ]
