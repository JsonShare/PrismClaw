"""专家包注册表。

v4 重构：
- ``experts`` 以 ``ExpertID`` 字符串（``name@experts``）为 key
- 删除 ``get_expert(name)``，新增 ``by_id(expert_id)`` 和 ``by_name(name)``
- ``by_state`` 等查询方法基于 ``ExpertID``
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from prismclaw.engine.experts.registry.descriptors import (
    AgentDescriptor,
    CommandDescriptor,
    ExpertDescriptor,
    SkillDescriptor,
)

if TYPE_CHECKING:
    from prismclaw.engine.experts.lifecycle.state import ExpertLifecycleState
    from prismclaw.engine.experts.registry.identity import ExpertID


@dataclass(slots=True)
class DescriptorRegistry:
    """专家包能力注册表。

    experts 以 ``ExpertID.canonical`` (``name@experts``) 为 key。
    by_name 用于按名称查找唯一匹配。
    """

    experts: dict[str, ExpertDescriptor] = field(default_factory=dict)
    skills: dict[str, SkillDescriptor] = field(default_factory=dict)
    commands: dict[str, CommandDescriptor] = field(default_factory=dict)
    agents: dict[str, AgentDescriptor] = field(default_factory=dict)

    def clear(self) -> None:
        self.experts.clear()
        self.skills.clear()
        self.commands.clear()
        self.agents.clear()

    def register_expert(self, descriptor: ExpertDescriptor) -> None:
        """以 ExpertID.canonical 注册。"""
        key = descriptor.expert_id.canonical if descriptor.expert_id else descriptor.canonical_id
        self.experts[key] = descriptor

    def register_skill(self, descriptor: SkillDescriptor) -> None:
        self.skills[descriptor.canonical_id] = descriptor

    def register_command(self, descriptor: CommandDescriptor) -> None:
        self.commands[descriptor.canonical_id.lower()] = descriptor

    def register_agent(self, descriptor: AgentDescriptor) -> None:
        self.agents[descriptor.canonical_id] = descriptor

    def by_id(self, expert_id: ExpertID) -> ExpertDescriptor | None:
        """按 ExpertID 精确查找。"""
        return self.experts.get(expert_id.canonical)

    def by_name(self, name: str) -> ExpertDescriptor | None:
        """按专家包名查找，返回唯一匹配；重名视为无匹配。"""
        candidates = [desc for desc in self.experts.values() if desc.expert_name == name]
        if len(candidates) != 1:
            return None
        return candidates[0]

    def get_command(self, command_name: str) -> CommandDescriptor | None:
        return self.commands.get(command_name.lower())

    def by_state(self, state: ExpertLifecycleState) -> list[ExpertDescriptor]:
        """按生命周期状态过滤专家包。"""
        return [desc for desc in self.experts.values() if desc.lifecycle_state == state]
