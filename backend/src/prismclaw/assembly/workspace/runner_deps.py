from __future__ import annotations

import dataclasses
from pathlib import Path

from prismclaw.assembly.experts.expert_lifecycle import ExpertLifecycleManager
from prismclaw.engine.execution.loop.deps import AgentRunnerDeps, ExpertBundle
from prismclaw.engine.toolkit.registry.registry import ToolRegistry
from prismclaw.infra.paths import build_project_capability_layout
from prismclaw.infra.settings import Settings


def _normalized(path: Path | str) -> Path:
    """规范化路径（展开 ~ 并 resolve）。纯计算，不触盘。"""
    return Path(path).expanduser().resolve()


def _copy_tool_registry(base_registry: ToolRegistry) -> ToolRegistry:
    tools = ToolRegistry()
    for name in base_registry.names():
        tool = base_registry.get(name)
        if tool is not None:
            tools.register(tool)
    return tools


async def build_workspace_scoped_runner_deps(
    base_deps: AgentRunnerDeps,
    *,
    settings: Settings,
    workspace_root: Path,
) -> AgentRunnerDeps:
    from prismclaw.runtime.capability_refresh import (
        refresh_runner_capabilities,
        schedule_mcp_capability_refresh,
    )

    if not isinstance(base_deps, AgentRunnerDeps):
        raise TypeError(
            "build_workspace_scoped_runner_deps requires AgentRunnerDeps; "
            "workspace-scoped runtime assembly cannot rebind arbitrary objects"
        )
    base_project_root = getattr(
        base_deps.expert.lifecycle_manager,
        "_project_root",
        None,
    )
    scoped_project_root = base_project_root if base_project_root is not None else workspace_root
    additional_project_roots: list[Path | str] | None = (
        [workspace_root]
        if _normalized(workspace_root) != _normalized(scoped_project_root)
        else None
    )

    # deps 边界：项目能力布局只构造这一次，下发给专家包 manager / 技能 provider /
    # subagent registry，并随 bundle 携带诊断，杜绝三处各自手拼 root / 目录。
    capability_layout = build_project_capability_layout(
        primary=scoped_project_root,
        additional=additional_project_roots,
        skills_dir=settings.skills.workspace_skills_dir,
        extra_skills_dirs=(settings.skills.asset_skills_dir,),
        experts_dir=settings.experts.asset_experts_dir,
        agents_dir=settings.subagent.asset_subagents_dir,
    )

    expert_lifecycle_manager = ExpertLifecycleManager(
        settings,
        workspace_default=workspace_root,
        project_root=scoped_project_root,
        additional_project_roots=additional_project_roots,
        task_manager=None,
        base_mcp_manager=None,
        capability_layout=capability_layout,
    )
    expert_runtime = await expert_lifecycle_manager.get_bundle()
    registry = getattr(base_deps, "tools", None)
    if not isinstance(registry, ToolRegistry):
        raise TypeError("AgentRunnerDeps.tools must be ToolRegistry for workspace scoping")
    scoped_deps = dataclasses.replace(
        base_deps,
        tools=_copy_tool_registry(registry),
        skill_provider=expert_runtime.skill_provider,
        expert=ExpertBundle(
            extension_manager=expert_runtime.extension_manager,
            lifecycle_manager=expert_lifecycle_manager,
            runtime_mcp_manager=base_deps.expert.runtime_mcp_manager,
            environment_catalog=base_deps.expert.environment_catalog,
            expert_registry=base_deps.expert.expert_registry,
            capability_policy_registry=base_deps.expert.capability_policy_registry,
        ),
    )
    refresh_runner_capabilities(
        scoped_deps,
        settings=settings,
        workspace_path=workspace_root,
        extension_manager=expert_runtime.extension_manager,
        skill_provider=expert_runtime.skill_provider,
        project_root=scoped_project_root,
        capability_layout=capability_layout,
    )
    # 与 create_agent_runner_deps 相同的冷启动时序缺口：若 MCP 尚未就绪，
    # 上面的同步 refresh 看不到 MCP 技能。就绪后一次性事件驱动重建。
    schedule_mcp_capability_refresh(
        scoped_deps,
        settings=settings,
        workspace_path=workspace_root,
        extension_manager=expert_runtime.extension_manager,
        skill_provider=expert_runtime.skill_provider,
        project_root=Path(scoped_project_root) if scoped_project_root else None,
        capability_layout=capability_layout,
    )
    return scoped_deps
