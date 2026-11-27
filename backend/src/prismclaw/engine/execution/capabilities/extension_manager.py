"""ExtensionManager：专家包能力注册中心。

- 所有数据存储在 ``_descriptor_registry``
- 入口：``register_loaded_expert`` / ``load_from_load_result``
- v5 渐进式披露：skill 注册时传递 ``pipeline_context`` 和 ``expert_name``
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from prismclaw.engine.commands.expert.expert_runtime import (
    ExpertCommandRuntime,
    build_expert_command_runtime,
)
from prismclaw.engine.experts.registry import DescriptorRegistry
from prismclaw.engine.experts.registry.descriptors import (
    AgentDescriptor,
    CommandDescriptor,
    ExpertDescriptor,
    SkillDescriptor,
)
from prismclaw.schema.agent.domains import SkillSource
from prismclaw.schema.services.skills import SkillManifest

if TYPE_CHECKING:
    from prismclaw.engine.experts.loading.load_result import ExpertPackage, PackageLoadResult
    from prismclaw.engine.experts.registry.identity import ExpertID

logger = logging.getLogger(__name__)


class ExtensionManager:
    """专家包能力注册中心。

    所有数据存储在 ``_descriptor_registry``。
    入口是 ``register_loaded_expert`` / ``load_from_load_result``。
    """

    def __init__(self) -> None:
        self._descriptor_registry = DescriptorRegistry()

    # ------------------------------------------------------------------
    # 注册入口
    # ------------------------------------------------------------------

    def register_loaded_expert(
        self,
        loaded_expert: ExpertPackage,
    ) -> None:
        """注册一个已加载专家包。

        直接从 ExpertPackage 结构化字段构建 ExpertDescriptor。
        """
        from prismclaw.engine.experts.lifecycle.state import ExpertLifecycleState
        from prismclaw.engine.experts.registry.identity import ExpertID

        expert_id = ExpertID(name=loaded_expert.name, source=loaded_expert.source)
        descriptor = self._build_expert_descriptor(loaded_expert, expert_id)
        if descriptor is None:
            return

        descriptor.lifecycle_state = ExpertLifecycleState.LOADED
        descriptor.explicitly_configured = True

        self._descriptor_registry.register_expert(descriptor)
        self._register_declarative_components(descriptor)

    def load_from_load_result(self, result: PackageLoadResult) -> None:
        """从 PackageLoadResult 批量注册专家包。"""
        for expert in result.enabled:
            self.register_loaded_expert(expert)

    # ------------------------------------------------------------------
    # Descriptor 构建
    # ------------------------------------------------------------------

    def _build_expert_descriptor(
        self,
        loaded: ExpertPackage,
        expert_id: ExpertID,
    ) -> ExpertDescriptor | None:
        """从 ExpertPackage 结构化字段直接构建 ExpertDescriptor。"""
        expert_name = loaded.expert_name or loaded.name

        return ExpertDescriptor(
            canonical_id=expert_id.canonical,
            expert_name=expert_name,
            namespace=loaded.source,
            origin=SkillSource.EXPERT.value,
            root_path=loaded.root_path,
            skills=list(loaded.skills),
            inherited_skills=list(getattr(loaded, "inherited_skills", []) or []),
            agents=list(loaded.agents),
            commands=list(loaded.commands),
            output_styles=dict(loaded.output_styles),
            expert_id=expert_id,
            expert_description=(
                getattr(loaded.manifest, "description", None) if loaded.manifest else None
            ),
        )

    def _register_declarative_components(self, descriptor: ExpertDescriptor) -> None:
        """从 ExpertDescriptor 注册 skills/agents/commands/hooks。"""
        expert_namespace = descriptor.expert_name
        skills = descriptor.skills
        agents = descriptor.agents
        commands = descriptor.commands

        for skill in skills:
            if not isinstance(skill, SkillManifest):
                continue
            namespaced_name = self._namespaced_capability_name(expert_namespace, skill.name)
            copied_skill = skill.model_copy(
                update={
                    "name": namespaced_name,
                    "source": SkillSource.EXPERT,
                    "loaded_from": skill.loaded_from or expert_namespace,
                    # v5 渐进式披露：显式深拷贝 pipeline_context，避免浅拷贝交叉污染
                    "pipeline_context": dict(skill.pipeline_context),
                }
            )
            self._descriptor_registry.register_skill(
                SkillDescriptor(
                    canonical_id=namespaced_name,
                    expert_name=expert_namespace,
                    origin=SkillSource.EXPERT.value,
                    manifest=copied_skill,
                    # v5 渐进式披露：pipeline_context 从原始 skill 对象复制
                    pipeline_context=dict(skill.pipeline_context),
                    expert_description=descriptor.expert_description,
                )
            )

        # 继承的全局基础技能：按裸名注册为全局能力（expert_name=None），
        # 不拼接专家前缀——它们不属于任何专家包。多个专家继承同一技能时
        # 只保留首次注册，避免 catalog 出现 N 份副本导致裸名调用歧义。
        # 直接复用加载产物对象，不做 model_copy：inherited_skills 仅用于
        # 展示与勾选校验，没有代码读取其 body，共享对象不会产生副作用。
        for skill in descriptor.inherited_skills:
            if not isinstance(skill, SkillManifest):
                continue
            if skill.name in self._descriptor_registry.skills:
                continue
            self._descriptor_registry.register_skill(
                SkillDescriptor(
                    canonical_id=skill.name,
                    expert_name=None,
                    origin=SkillSource.BUNDLED.value,
                    manifest=skill,
                    pipeline_context=dict(skill.pipeline_context),
                )
            )

        for agent in agents:
            if not hasattr(agent, "model_copy") or not hasattr(agent, "name"):
                continue
            namespaced_name = self._namespaced_capability_name(
                expert_namespace, getattr(agent, "name", "")
            )
            from prismclaw.engine.execution.skills.agent_adapter import derive_skill_agent_name
            from prismclaw.engine.subagent.definition.manifest import AgentManifest

            runtime_name = derive_skill_agent_name(
                namespaced_name,
                explicit_name=f"{expert_namespace}-{getattr(agent, 'name', '')}",
            )
            model_dump = getattr(agent, "model_dump", None)
            payload = model_dump(mode="json", exclude_none=True) if callable(model_dump) else {}
            if not isinstance(payload, dict):
                payload = {}
            payload["name"] = runtime_name
            copied_agent = AgentManifest.model_validate(payload)
            self._descriptor_registry.register_agent(
                AgentDescriptor(
                    canonical_id=namespaced_name,
                    expert_name=expert_namespace,
                    origin=SkillSource.EXPERT.value,
                    manifest=copied_agent,
                    expert_description=descriptor.expert_description,
                )
            )

        for command in commands:
            command_name = getattr(command, "name", None)
            if not isinstance(command_name, str) or not command_name:
                continue
            self._descriptor_registry.register_command(
                CommandDescriptor(
                    canonical_id=command_name,
                    expert_name=expert_namespace,
                    origin=SkillSource.EXPERT.value,
                    capability=command,
                    expert_description=descriptor.expert_description,
                )
            )

        logger.info(
            "Registered declarative components for %s: %d skills, %d agents, %d commands.",
            expert_namespace,
            len(skills),
            len(agents),
            len(commands),
        )

    @staticmethod
    def _namespaced_capability_name(expert_name: str, base_name: str) -> str:
        """Build the canonical namespaced capability id.

        Namespacing is unconditional: ``base_name`` is always the expert-local
        name taken verbatim from a fresh SKILL.md / agent manifest parse, never
        a pre-namespaced id. Registration is therefore idempotent *structurally*
        — :meth:`DescriptorRegistry.register_skill` overwrites by ``canonical_id``
        and expert reload builds a fresh registry — so no content-based guard is
        needed here. Sniffing ``base_name`` for an existing prefix would be both
        dead code and a latent bug (a skill literally named ``foo:bar`` under
        expert ``foo`` would wrongly skip namespacing).
        """
        return f"{expert_name}:{base_name}"

    # ------------------------------------------------------------------
    # 只读查询 API
    # ------------------------------------------------------------------

    @property
    def registry(self) -> DescriptorRegistry:
        return self._descriptor_registry

    def iter_expert_descriptors(self) -> list[ExpertDescriptor]:
        return list(self._descriptor_registry.experts.values())

    def get_expert_descriptor(self, expert_name: str) -> ExpertDescriptor | None:
        """按专家包名查找（跨源，返回最高优先级）。"""
        return self._descriptor_registry.by_name(expert_name)

    def get_expert_descriptor_by_id(self, expert_id: ExpertID) -> ExpertDescriptor | None:
        """按 ExpertID 精确查找。"""
        return self._descriptor_registry.by_id(expert_id)

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
        """解析命令名，兼容 ``/<namespace>:<command>`` 命名空间写法。

        专家包文档普遍按 ``/<subpackage>:<command>`` 书写，而 commands/ 目录下的命令
        以裸名注册（部分还带 ``<短名>-`` 前缀去重）。解析顺序：

        1. 精确匹配注册名（原有行为）
        2. ``expert`` 前缀 + 同专家包内的注册名
        3. ``expert`` 前缀 + 同专家包 commands/*.md 的文件名（stem）
        4. ``expert`` 前缀 + 同专家包内以 ``-<command>`` 结尾的去重名
        """
        if not command_name:
            return None
        descriptor = self._descriptor_registry.get_command(command_name)
        if descriptor is not None or ":" not in command_name:
            return descriptor

        expert, _, suffix = command_name.lower().partition(":")
        if not expert or not suffix:
            return None

        candidates = [
            command
            for command in self._descriptor_registry.commands.values()
            if (command.expert_name or "").lower() == expert
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

    def get_expert_command_descriptor(self, command_name: str) -> CommandDescriptor | None:
        return self._resolve_command_descriptor(command_name)

    def iter_expert_command_descriptors(self) -> list[CommandDescriptor]:
        return list(self._descriptor_registry.commands.values())

    def iter_agent_descriptors(self) -> list[AgentDescriptor]:
        return list(self._descriptor_registry.agents.values())

    def get_expert_command_runtime(self, command_name: str) -> ExpertCommandRuntime | None:
        descriptor = self._resolve_command_descriptor(command_name)
        if descriptor is not None and descriptor.capability is not None and descriptor.expert_name:
            return build_expert_command_runtime(descriptor.expert_name, descriptor.capability)
        return None


_global_extension_manager: ExtensionManager | None = None


def get_extension_manager() -> ExtensionManager:
    """全局 ExtensionManager 单例。"""
    global _global_extension_manager
    if _global_extension_manager is None:
        _global_extension_manager = ExtensionManager()
    return _global_extension_manager
