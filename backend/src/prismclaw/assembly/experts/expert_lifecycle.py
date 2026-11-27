"""ExpertLifecycleManager：构建 runner-scoped 专家包运行时 bundle。

- 异步 ``get_bundle()`` / ``rebuild()``
- ``_build_extension_manager`` 创建空 ``ExtensionManager``，由 ``load_from_load_result`` 填充
- ``get_bundle`` 走新流水线：``assemble_expert_load_result`` → ``load_from_load_result``
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from prismclaw.infra.paths import build_project_capability_layout
from prismclaw.infra.settings import Settings
from prismclaw.infra.task_manager import TaskManager
from prismclaw.schema.shared import CapabilityKind, ProjectCapabilityLayout

if TYPE_CHECKING:
    from prismclaw.engine.execution.capabilities.extension_manager import ExtensionManager
    from prismclaw.engine.experts.registry.expert_registry import ExpertRegistry
    from prismclaw.engine.mcp.client_manager import MCPClientManager

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ExpertRuntimeBundle:
    extension_manager: Any
    skill_provider: Any
    runtime_mcp_manager: Any = None
    expert_registry: ExpertRegistry | None = None
    capability_layout: ProjectCapabilityLayout | None = None


@dataclass(frozen=True, slots=True)
class ExpertRuntimeCandidate:
    bundle: ExpertRuntimeBundle
    generation: int


class ExpertLifecycleManager:
    """构建 runner-scoped 专家包运行时 bundle。

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
        base_mcp_manager: MCPClientManager | None = None,
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
        self._bundle: ExpertRuntimeBundle | None = None
        self._generation = 0

    def _capability_layout(self) -> ProjectCapabilityLayout:
        """返回本 manager 使用的唯一项目能力布局（注入优先，否则缓存现场构造）。"""
        if self._injected_layout is not None:
            return self._injected_layout
        if self._cached_layout is None:
            self._cached_layout = build_project_capability_layout(
                primary=(
                    Path(self._project_root)
                    if isinstance(self._project_root, str)
                    else self._project_root
                ),
                additional=self._additional_project_roots,
                skills_dir=self._settings.skills.workspace_skills_dir,
                extra_skills_dirs=(self._settings.skills.asset_skills_dir,),
                experts_dir=self._settings.experts.asset_experts_dir,
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

    async def get_bundle(self) -> ExpertRuntimeBundle:
        """异步构建 bundle。v4：唯一入口。"""
        if self._bundle is None:
            self._bundle = await self._build_bundle()
        return self._bundle

    async def rebuild(self) -> ExpertRuntimeBundle:
        """强制重建 bundle。v4：async。"""
        candidate = await self.build_candidate()
        return self.commit(candidate)

    async def build_candidate(self) -> ExpertRuntimeCandidate:
        """完整构建候选，但不改写当前缓存或 generation。"""
        bundle = await self._build_bundle()
        return ExpertRuntimeCandidate(bundle=bundle, generation=self._generation + 1)

    def commit(self, candidate: ExpertRuntimeCandidate) -> ExpertRuntimeBundle:
        """同步提交已验证候选。"""
        self._bundle = candidate.bundle
        self._generation = candidate.generation
        return candidate.bundle

    def invalidate(self) -> None:
        self._generation += 1
        self._bundle = None

    def update_settings(self, settings: object, *, invalidate: bool = True) -> None:
        # schema 层的 ExpertLifecycleProtocol 只声明「接受一个设置对象」，
        # 这里收窄回具体 Settings；非 Settings 直接忽略（fail-closed）。
        if not isinstance(settings, Settings):
            return
        self._settings = settings
        # 目录契约来自 settings，settings 变更后缓存布局需重建（注入布局不受影响）。
        self._cached_layout = None
        if invalidate:
            self.invalidate()

    def _build_extension_manager(self) -> ExtensionManager:
        """v4：创建空 ExtensionManager（无 loader 注册）。"""
        from prismclaw.engine.execution.capabilities.extension_manager import ExtensionManager

        return ExtensionManager()

    async def _build_bundle(self) -> ExpertRuntimeBundle:
        """v4：唯一 bundle 构建路径，全面异步化。"""
        from prismclaw.runtime.skills.provider import DefaultSkillProvider

        # 唯一能力布局：专家包目录与技能 provider 共享同一次展开结果。
        layout = self._capability_layout()

        directories = list(layout.existing_locations_of(CapabilityKind.EXPERTS))

        extension_manager = self._build_extension_manager()
        from prismclaw.assembly.experts.load_wiring import assemble_expert_load_result

        load_result = await assemble_expert_load_result(directories=directories)
        from prismclaw.engine.experts.registry.expert_registry import (
            ExpertRegistry,
            ExpertRegistryIssue,
        )

        expert_registry = ExpertRegistry(
            load_result.all_experts,
            issues=[
                ExpertRegistryIssue(
                    code=str(error.type),
                    expert_id=error.expert or "",
                    message=error.format_message(),
                )
                for error in load_result.errors
            ],
        )
        logger.info(
            "expert registry loaded %d experts with %d issues",
            len(expert_registry.list_experts()),
            len(expert_registry.issues),
        )
        extension_manager.load_from_load_result(load_result)

        return ExpertRuntimeBundle(
            extension_manager=extension_manager,
            skill_provider=DefaultSkillProvider(
                settings=self._settings.skills,
                project_root=self._project_root,
                additional_project_roots=self._additional_project_roots,
                project_locations=layout.existing_locations_of(CapabilityKind.SKILLS),
            ),
            runtime_mcp_manager=self._base_mcp_manager,
            expert_registry=expert_registry,
            capability_layout=layout,
        )
