from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Callable, TYPE_CHECKING

from prismclaw.engine.execution.capabilities.catalog import CapabilityCatalogService
from prismclaw.engine.execution.capabilities.runtime_bundle import RuntimeCapabilityBundle
from prismclaw.engine.execution.loop.bootstrap import AgentRunnerDeps
from prismclaw.engine.execution.skills.runtime import SkillRuntime
from prismclaw.engine.toolkit.registry.registry import ToolRegistry
from prismclaw.infra.settings import Settings
from prismclaw.schema.shared import CapabilityKind, ProjectCapabilityLayout
from prismclaw.runtime.skills.mcp_skills import mcp_prompt_resolver, mcp_skill_manifests_builder

if TYPE_CHECKING:
    from prismclaw.schema.config.config import SubAgentConfig

logger = logging.getLogger(__name__)

# Strong references to in-flight refresh tasks (create_task results are weakly
# held by the loop; without this the one-shot refresh could be GC'd mid-await).
_PENDING_REFRESH_TASKS: set[asyncio.Task] = set()


def build_subagent_registry(
    settings: Settings,
    *,
    workspace_path: Path,
    project_root: Path | None = None,
    additional_project_roots: list[Path | str] | None = None,
    capability_layout: ProjectCapabilityLayout | None = None,
) -> Any:
    from prismclaw.engine.subagent import SubAgentRegistry

    # 优先复用 deps 边界已构造的唯一布局；否则由 registry 以 workspace 为根现场展开。
    agent_locations = (
        capability_layout.locations_of(CapabilityKind.AGENTS)
        if capability_layout is not None
        else None
    )
    return SubAgentRegistry(
        settings.subagent,
        workspace_path=workspace_path,
        project_root=project_root,
        additional_project_roots=additional_project_roots,
        agent_locations=agent_locations,
    )


def build_capability_catalog(
    *,
    workspace_path: Path,
    tool_registry: ToolRegistry,
    skill_provider: Any,
    extension_manager: Any,
    subagent_registry: Any = None,
    mcp_skill_manifests_builder: Callable[[], list[Any]] | None = None,
) -> CapabilityCatalogService:
    from prismclaw.engine.execution.capabilities.assembly import assemble_capability_catalog

    return assemble_capability_catalog(
        workspace_path=workspace_path,
        skill_provider=skill_provider,
        extension_manager=extension_manager,
        subagent_registry=subagent_registry,
        tool_registry=tool_registry,
        mcp_skill_manifests_builder=mcp_skill_manifests_builder,
    )


def _extract_memory_store_from_deps(deps: AgentRunnerDeps) -> Any:
    service_bundle = getattr(getattr(deps, "context_engine", None), "service_bundle", None)
    memory_retriever = getattr(service_bundle, "memory_retriever", None)
    return getattr(memory_retriever, "_memory_store", None)


def register_subagent_tools(
    registry: ToolRegistry,
    *,
    settings: Settings,
    capability_catalog: Any,
    subagent_registry: Any,
    llm_client: Any,
    memory_store: Any,
    skill_runtime: Any,
    channel: str | None = None,
) -> "SubAgentConfig":
    """Register the sub-agent tool trio (sub_agent / reduce / handoff).

    Single wiring point shared by startup assembly (runner_assembly) and
    runtime capability refresh (refresh_runner_capabilities), so the
    SubAgentExecutor construction cannot drift between the two paths.
    Idempotent: existing registrations are replaced; at startup the names
    simply do not exist yet and unregister is a no-op.

    The runner-level ``settings.subagent`` (SubAgentSettings) is validated
    into the strong-typed SubAgentConfig that SubAgentExecutor and
    SubAgentTool expect.

    ``channel`` is only the executor's initial default; every sub-agent run
    overrides it from ``parent_ctx.channel`` (_lifecycle_mixin). Startup has
    no runner channel yet, hence optional.

    Returns the validated SubAgentConfig (the startup handoff supervisor
    reuses it).
    """
    from prismclaw.engine.execution.handoff.request_handoff import RequestHandoffTool
    from prismclaw.engine.subagent import SubAgentExecutor, SubAgentTool
    from prismclaw.engine.toolkit.subagent.reduce_subagent_results import (
        ReduceSubAgentResultsTool,
    )
    from prismclaw.schema.config.config import SubAgentConfig

    subagent_config = SubAgentConfig.model_validate(settings.subagent.model_dump())

    for name in ("sub_agent", "reduce_subagent_results", "request_handoff"):
        registry.unregister(name)

    if not settings.subagent.enabled:
        return subagent_config
    if capability_catalog.count_agent_entries() <= 0:
        return subagent_config

    executor = SubAgentExecutor(
        tool_registry=registry,
        settings=subagent_config,
        llm_client=llm_client,
        memory_store=memory_store,
        default_model=settings.models.default_model,
        decision_audit_logger=None,
        channel=channel or "web",
        skill_runtime=skill_runtime,
        # 子代理内部工具遵守与主流程相同的 agent 配置：agent.timeouts.tool_seconds
        # （注意：此处 settings 是 infra Settings，timeouts 在 agent 子块下，
        #  等价于主流程 deps.config.timeouts.tool_seconds 读到的同一个值）
        tool_timeout_s=settings.agent.timeouts.tool_seconds,
    )
    registry.register(
        SubAgentTool(
            executor=executor,
            registry=subagent_registry,
            settings=subagent_config,
            capability_catalog=capability_catalog,
        )
    )
    registry.register(ReduceSubAgentResultsTool())
    registry.register(RequestHandoffTool())
    return subagent_config


def schedule_mcp_capability_refresh(
    deps: AgentRunnerDeps,
    *,
    settings: Settings,
    workspace_path: Path,
    extension_manager: Any,
    skill_provider: Any,
    project_root: Path | None = None,
    capability_layout: ProjectCapabilityLayout | None = None,
) -> asyncio.Task | None:
    """One-shot, event-driven catalog rebuild once MCP reaches ready state.

    The initial capability catalog is assembled synchronously during startup,
    *before* the MCP background supervisor finishes connecting — MCP
    prompt-type skills are therefore invisible in the first build. This
    schedules a rebuild keyed on the manager's ``wait_ready`` protocol method
    (no polling). Managers without ``wait_ready`` (or absent managers) yield
    ``None`` — there is nothing to wait for.
    """
    manager = getattr(deps.plugin, "runtime_mcp_manager", None)
    waiter = getattr(manager, "wait_ready", None)
    if not callable(waiter):
        return None

    async def _refresh_when_ready() -> None:
        await waiter()
        try:
            refresh_runner_capabilities(
                deps,
                settings=settings,
                workspace_path=workspace_path,
                extension_manager=extension_manager,
                skill_provider=skill_provider,
                project_root=project_root,
                capability_layout=capability_layout,
            )
            catalog = deps.capability_catalog
            n_skills = len(catalog.list_skill_entries()) if catalog is not None else 0
            logger.info(
                "capability catalog refreshed after MCP ready (skills=%d)", n_skills
            )
        except Exception:
            logger.exception("capability refresh after MCP ready failed")

    task = asyncio.create_task(_refresh_when_ready(), name="mcp:capability-refresh")
    _PENDING_REFRESH_TASKS.add(task)
    task.add_done_callback(_PENDING_REFRESH_TASKS.discard)
    return task


def refresh_runner_capabilities(
    deps: AgentRunnerDeps,
    *,
    settings: Settings,
    workspace_path: Path,
    extension_manager: Any,
    skill_provider: Any,
    project_root: Path | None = None,
    subagent_registry_override: Any = None,
    refresh_mcp_tools: bool = False,
    capability_layout: ProjectCapabilityLayout | None = None,
) -> RuntimeCapabilityBundle:
    from prismclaw.engine.toolkit.registry.discovery import SkillSearchTool, ToolSearchTool
    from prismclaw.engine.toolkit.builtin.skill_activate import ActivateSkillTool
    from prismclaw.engine.toolkit.mcp.fetch_mcp_resource import FetchMcpResourceTool

    if not isinstance(deps, AgentRunnerDeps):
        raise TypeError("refresh_runner_capabilities requires AgentRunnerDeps")

    registry = getattr(deps, "tools", None)
    if not isinstance(registry, ToolRegistry):
        raise TypeError("AgentRunnerDeps.tools must be ToolRegistry for capability refresh")

    current_names = set(registry.names())

    subagent_registry = subagent_registry_override
    if subagent_registry is None and settings.subagent.enabled:
        extra_project_roots = (
            [workspace_path]
            if project_root is not None and workspace_path.resolve() != project_root.resolve()
            else None
        )
        subagent_registry = build_subagent_registry(
            settings,
            workspace_path=workspace_path,
            project_root=project_root,
            additional_project_roots=extra_project_roots,
            capability_layout=capability_layout,
        )

    runtime_mcp_manager = getattr(deps.plugin, "runtime_mcp_manager", None)
    mcp_builder = mcp_skill_manifests_builder(runtime_mcp_manager)

    capability_catalog = build_capability_catalog(
        workspace_path=workspace_path,
        tool_registry=registry,
        skill_provider=skill_provider,
        extension_manager=extension_manager,
        subagent_registry=subagent_registry,
        mcp_skill_manifests_builder=mcp_builder,
    )
    # Inject skill_parser callback (P4: engine→skills decoupling)
    from prismclaw.runtime.skills.parser import parse_skill_file
    skill_runtime = SkillRuntime(
        capability_catalog=capability_catalog,
        mcp_prompt_resolver=mcp_prompt_resolver(runtime_mcp_manager),
        descriptor_registry=extension_manager._descriptor_registry if extension_manager else None,
        skill_parser=parse_skill_file,
    )

    # 同步插件级指令（PRISMCLAW.md）到 SkillRuntime
    if extension_manager is not None and hasattr(extension_manager, "iter_plugin_descriptors"):
        for descriptor in extension_manager.iter_plugin_descriptors():
            instr = getattr(descriptor, "plugin_instructions", None)
            pname = getattr(descriptor, "plugin_name", None)
            if instr and pname:
                skill_runtime.register_plugin_instructions(pname, instr)

    deps.capability_catalog = capability_catalog
    deps.skill_runtime = skill_runtime

    if "tool_search" in current_names:
        registry.unregister("tool_search")
        registry.register(ToolSearchTool(capability_catalog=capability_catalog))

    if "skill_search" in current_names:
        registry.unregister("skill_search")
        registry.register(SkillSearchTool(capability_catalog=capability_catalog))

    # activate_skill holds a reference to the skill runtime; re-register it
    # together with the freshly rebuilt catalog/runtime pair.
    if "activate_skill" in current_names:
        registry.unregister("activate_skill")
    registry.register(
        ActivateSkillTool(
            skill_runtime=skill_runtime,
            capability_catalog=capability_catalog,
        )
    )

    if "fetch_mcp_resource" in current_names:
        registry.unregister("fetch_mcp_resource")
        runtime_mcp_manager = getattr(deps.plugin, "runtime_mcp_manager", None)
        if runtime_mcp_manager is not None:
            registry.register(FetchMcpResourceTool(runtime_mcp_manager))

    if refresh_mcp_tools:
        for name in list(registry.names()):
            tool = registry.get(name)
            if getattr(tool, "server_config", None) is not None:
                registry.unregister(name)
        runtime_mcp_manager = getattr(deps.plugin, "runtime_mcp_manager", None)
        if runtime_mcp_manager is not None:
            runtime_mcp_manager.attach_and_register(registry)

    register_subagent_tools(
        registry,
        settings=settings,
        capability_catalog=capability_catalog,
        subagent_registry=subagent_registry,
        llm_client=deps.llm,
        memory_store=_extract_memory_store_from_deps(deps),
        skill_runtime=skill_runtime,
        channel=deps.channel,
    )

    return RuntimeCapabilityBundle(
        workspace_path=workspace_path,
        tool_registry=registry,
        capability_catalog=capability_catalog,
        skill_runtime=skill_runtime,
        subagent_registry=subagent_registry,
        extension_manager=extension_manager,
        skill_provider=skill_provider,
        project_root=project_root,
    )
