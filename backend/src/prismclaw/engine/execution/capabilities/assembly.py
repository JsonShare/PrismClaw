from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterable

logger = logging.getLogger(__name__)

from prismclaw.schema.agent.capability import (
    CapabilityGraph,
    CapabilityKind,
    CapabilityNode,
    PluginSourceRef,
    RuntimeCapabilities,
    ScopeKind,
    SourceKind,
    TrustLevel,
)
from prismclaw.engine.execution.capabilities.graph_assembler import CapabilityAssembler
from prismclaw.engine.execution.capabilities.catalog import CapabilityCatalogService
from prismclaw.engine.toolkit.registry.registry import ToolRegistry

if TYPE_CHECKING:
    from prismclaw.schema.agent.capability import PluginCommandCapability
    from prismclaw.schema.services.skills import SkillManifest


def assemble_capability_catalog(
    *,
    workspace_path: str | Path | None = None,
    skill_provider: Any = None,
    extension_manager: Any = None,
    subagent_registry: Any = None,
    tool_registry: ToolRegistry | None = None,
    mcp_skill_manifests_builder: Callable[[], list[Any]] | None = None,
) -> CapabilityCatalogService:
    capability_graph, runtime_capabilities = _assemble_catalog_snapshot(
        tool_registry=tool_registry,
        workspace_path=workspace_path,
        extension_manager=extension_manager,
        skill_provider=skill_provider,
        subagent_registry=subagent_registry,
        mcp_skill_manifests_builder=mcp_skill_manifests_builder,
    )

    return CapabilityCatalogService(
        tool_registry=tool_registry,
        capability_graph=capability_graph,
        runtime_capabilities=runtime_capabilities,
    )


def _load_provider_skill_manifests(
    skill_provider: Any,
    *,
    workspace_path: str | Path | None,
) -> list[Any]:
    if skill_provider is None:
        logger.debug("skill_provider is None, skipping provider skill manifests")
        return []
    list_skills = getattr(skill_provider, "list_skills", None)
    if not callable(list_skills):
        logger.warning("skill_provider has no callable list_skills (type=%s)", type(skill_provider).__name__)
        return []
    try:
        if workspace_path is not None:
            skills = list(list_skills(str(workspace_path)))
        else:
            skills = list(list_skills())
        logger.info("skill_provider.list_skills returned %d skills (workspace_path=%s)", len(skills), workspace_path)
        return skills
    except TypeError:
        skills = list(list_skills())
        logger.info("skill_provider.list_skills (no-arg) returned %d skills", len(skills))
        return skills
    except Exception:
        logger.warning("skill_provider.list_skills failed", exc_info=True)
        return []


def _dedupe_by_name(items: Iterable[Any]) -> list[Any]:
    seen: set[str] = set()
    deduped: list[Any] = []
    for item in items:
        name = getattr(item, "name", None)
        if not isinstance(name, str) or not name or name in seen:
            continue
        seen.add(name)
        deduped.append(item)
    return deduped


def _safe_resolve_path(raw: str | Path | None) -> Path | None:
    if raw is None:
        return None
    try:
        return Path(raw).expanduser().resolve()
    except Exception:
        return None


def _generic_source_ref(
    *,
    source_id: str,
    source_kind: SourceKind,
    trust_level: TrustLevel,
    origin_path: str | None = None,
) -> PluginSourceRef:
    return PluginSourceRef(
        source_id=source_id,
        source_kind=source_kind,
        trust_level=trust_level,
        scope_kind=ScopeKind.WORKSPACE,
        scope_id=origin_path,
        origin_path=origin_path,
    )


def _source_ref_for_workspace_manifest(
    *,
    canonical_id: str,
    location: str,
    workspace_path: str | Path | None,
) -> PluginSourceRef:
    resolved_location = _safe_resolve_path(location)
    resolved_workspace = _safe_resolve_path(workspace_path)
    origin_path = str(resolved_location) if resolved_location is not None else (location or None)
    if (
        resolved_location is not None
        and resolved_workspace is not None
        and (resolved_location == resolved_workspace or resolved_workspace in resolved_location.parents)
    ):
        return _generic_source_ref(
            source_id=f"workspace:{canonical_id}",
            source_kind=SourceKind.WORKSPACE,
            trust_level=TrustLevel.MEDIUM,
            origin_path=origin_path,
        )
    return _generic_source_ref(
        source_id=f"builtin:{canonical_id}",
        source_kind=SourceKind.BUILTIN,
        trust_level=TrustLevel.HIGH,
        origin_path=origin_path,
    )


def _source_ref_for_tool(tool: Any) -> PluginSourceRef:
    tool_name = getattr(tool, "name", None)
    canonical_id = tool_name if isinstance(tool_name, str) and tool_name else "unknown-tool"
    server_name = getattr(tool, "server_name", None)
    if isinstance(server_name, str) and server_name:
        return _generic_source_ref(
            source_id=f"mcp:{server_name}",
            source_kind=SourceKind.MCP,
            trust_level=TrustLevel.LOW,
            origin_path=None,
        )
    return _generic_source_ref(
        source_id=f"builtin-tool:{canonical_id}",
        source_kind=SourceKind.BUILTIN,
        trust_level=TrustLevel.HIGH,
        origin_path=None,
    )


def _source_ref_for_mcp_skill(server_name: str) -> PluginSourceRef:
    if not server_name:
        server_name = "unknown"
    return _generic_source_ref(
        source_id=f"mcp:{server_name}",
        source_kind=SourceKind.MCP,
        trust_level=TrustLevel.LOW,
        origin_path=None,
    )


def _dedupe_capability_nodes(nodes: Iterable[CapabilityNode]) -> list[CapabilityNode]:
    deduped: list[CapabilityNode] = []
    seen: set[tuple[CapabilityKind, str]] = set()
    for node in nodes:
        key = (node.kind, node.canonical_id)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(node)
    return deduped


def _assemble_catalog_snapshot(
    *,
    tool_registry: ToolRegistry | None,
    workspace_path: str | Path | None,
    extension_manager: Any,
    skill_provider: Any,
    subagent_registry: Any,
    mcp_skill_manifests_builder: Callable[[], list[Any]] | None = None,
) -> tuple[CapabilityGraph, RuntimeCapabilities]:
    assembler = CapabilityAssembler()
    nodes: list[CapabilityNode] = []

    def _dump_manifest_payload(manifest: Any) -> dict[str, Any]:
        model_dump = getattr(manifest, "model_dump", None)
        if callable(model_dump):
            payload = model_dump(mode="json", exclude_none=True)
            if isinstance(payload, dict):
                return payload
        raise TypeError(
            f"capability catalog snapshot requires pydantic-style model_dump() manifest "
            f"(got {type(manifest)!r})"
        )

    def _source_ref_for_plugin_name(plugin_name: str | None) -> PluginSourceRef:
        if not plugin_name:
            return _generic_source_ref(
                source_id="plugin:unknown",
                source_kind=SourceKind.LOCAL_PLUGIN,
                trust_level=TrustLevel.MEDIUM,
                origin_path=None,
            )
        namespace = ""
        origin_path: str | None = None
        if extension_manager is not None and hasattr(extension_manager, "get_plugin_descriptor"):
            descriptor = extension_manager.get_plugin_descriptor(plugin_name)
            if descriptor is not None:
                namespace = getattr(descriptor, "namespace", "") or ""
                root_path = getattr(descriptor, "root_path", None)
                if root_path is not None:
                    origin_path = str(root_path)
        source_kind = SourceKind.LOCAL_PLUGIN
        managed_ref: str | None = None
        if isinstance(namespace, str) and namespace.startswith("session:"):
            source_kind = SourceKind.SESSION_PLUGIN
        elif isinstance(namespace, str) and namespace.startswith("managed:"):
            source_kind = SourceKind.MANAGED_PLUGIN
            managed_ref = namespace
        return PluginSourceRef(
            source_id=f"plugin:{plugin_name}",
            source_kind=source_kind,
            trust_level=TrustLevel.MEDIUM,
            scope_kind=ScopeKind.WORKSPACE,
            scope_id=origin_path,
            origin_path=origin_path,
            managed_ref=managed_ref,
        )

    plugin_graphs_by_name: dict[str, CapabilityGraph] = {}
    if extension_manager is not None and hasattr(extension_manager, "iter_plugin_capability_graphs"):
        for view in extension_manager.iter_plugin_capability_graphs():
            graph = getattr(view, "graph", None)
            if not isinstance(graph, CapabilityGraph):
                continue
            run_id = getattr(graph, "run_id", "")
            if isinstance(run_id, str) and run_id.startswith("plugin:"):
                plugin_graphs_by_name[run_id.split(":", 1)[1]] = graph

    def _source_ref_for_plugin_capability(
        *,
        plugin_name: str | None,
        canonical_id: str,
    ) -> PluginSourceRef:
        if isinstance(plugin_name, str) and plugin_name in plugin_graphs_by_name:
            graph = plugin_graphs_by_name[plugin_name]
            node = graph.get(canonical_id)
            if node is not None:
                return node.source_ref
        return _source_ref_for_plugin_name(plugin_name)

    skill_manifests = _dedupe_by_name(
        _load_provider_skill_manifests(
            skill_provider,
            workspace_path=workspace_path,
        )
    )
    logger.info(
        "capability catalog: %d provider skills, extension_manager=%s, workspace_path=%s",
        len(skill_manifests),
        type(extension_manager).__name__ if extension_manager is not None else "None",
        workspace_path,
    )
    seen_skills = {getattr(m, "name", None) for m in skill_manifests if isinstance(getattr(m, "name", None), str)}
    extension_skill_fingerprints: list[str] = []

    for manifest in skill_manifests:
        name = getattr(manifest, "name", None)
        if not isinstance(name, str) or not name:
            continue
        location = getattr(manifest, "file_path", "") or ""
        nodes.append(
            assembler.make_enabled_node(
                canonical_id=name,
                kind=CapabilityKind.SKILL,
                display_name=name,
                source_ref=_source_ref_for_workspace_manifest(
                    canonical_id=name,
                    location=location,
                    workspace_path=workspace_path,
                ),
                metadata={
                    "plugin_name": None,
                    "origin": None,
                    "manifest": _dump_manifest_payload(manifest),
                },
                projection_targets=("skills",),
            )
        )

    if extension_manager is not None and hasattr(extension_manager, "iter_skill_descriptors"):
        for descriptor in extension_manager.iter_skill_descriptors():
            manifest = getattr(descriptor, "manifest", None)
            name = getattr(manifest, "name", None)
            if not isinstance(name, str) or not name or name in seen_skills:
                continue
            plugin_name = getattr(descriptor, "plugin_name", None)
            extension_skill_fingerprints.append(
                f"plugin-skill:{plugin_name or ''}:{name}:{getattr(manifest, 'file_path', '') or ''}"
            )
            nodes.append(
                assembler.make_enabled_node(
                    canonical_id=name,
                    kind=CapabilityKind.SKILL,
                    display_name=name,
                    source_ref=_source_ref_for_plugin_capability(
                        plugin_name=plugin_name,
                        canonical_id=name,
                    ),
                    metadata={
                        "plugin_name": plugin_name,
                        "origin": getattr(descriptor, "origin", None),
                        "manifest": _dump_manifest_payload(manifest),
                        # v5 渐进式披露：marketplace_name 用于系统提示词按包名聚合
                        "marketplace_name": getattr(descriptor, "marketplace_name", None),
                        # v5 渐进式披露：marketplace / 插件级描述（来自 marketplace.json description）
                        "marketplace_description": getattr(descriptor, "marketplace_description", None),
                        "plugin_description": getattr(descriptor, "plugin_description", None),
                    },
                    projection_targets=("skills",),
                )
            )

    extension_command_fingerprints: list[str] = []
    # 跨 plugin 重名 commands(Cold-start-interview/matter-workspace 等)允许分别注册,
    # seen_plugin_cmds 仅防止同一 plugin 下重名(正常不该发生)。
    seen_plugin_cmds: set[tuple[str | None, str]] = set()

    def _pseudo_skill_manifest_for_command(
        command: "PluginCommandCapability",
        *,
        plugin_name: str | None,
    ) -> "SkillManifest":
        """把 PluginCommandCapability 包装为伪 SkillManifest。

        commands 是原子技能（Phase 3 迁移产物），不编排子技能，
        因此 declared_invoke_sequence / preload_skills 为空是语义正确的。
        frontmatter 字段从 capability.metadata（command dict 完整拷贝）提取。
        """
        from prismclaw.schema.agent.domains import SkillSource
        from prismclaw.schema.services.skills import SkillManifest

        meta = command.metadata or {}
        allowed_tools = meta.get("allowed_tools")
        if not isinstance(allowed_tools, list):
            allowed_tools = None
        invocation_policy = meta.get("invocation_policy", "auto")
        if invocation_policy not in ("auto", "user_only", "disabled"):
            invocation_policy = "auto"
        category = meta.get("category")
        if not isinstance(category, str):
            category = None
        return SkillManifest(
            name=command.name,
            description=command.description,
            body=command.body,
            source=SkillSource.PLUGIN,
            loaded_from=plugin_name or "",
            preload_skills=[],
            declared_invoke_sequence=[],
            invocation_policy=invocation_policy,
            allowed_tools=allowed_tools,
            category=category,
        )

    if extension_manager is not None and hasattr(extension_manager, "iter_plugin_command_descriptors"):
        for cmd_descriptor in extension_manager.iter_plugin_command_descriptors():
            capability = getattr(cmd_descriptor, "capability", None)
            if capability is None:
                continue
            cmd_name = getattr(capability, "name", None)
            if not isinstance(cmd_name, str) or not cmd_name:
                continue
            plugin_name = getattr(cmd_descriptor, "plugin_name", None)
            dedup_key = (plugin_name, cmd_name)
            if dedup_key in seen_plugin_cmds:
                continue
            seen_plugin_cmds.add(dedup_key)
            extension_command_fingerprints.append(
                f"plugin-command:{plugin_name or ''}:{cmd_name}"
            )
            pseudo_manifest = _pseudo_skill_manifest_for_command(
                capability, plugin_name=plugin_name,
            )
            nodes.append(
                assembler.make_enabled_node(
                    canonical_id=cmd_name,
                    kind=CapabilityKind.SKILL,
                    display_name=cmd_name,
                    source_ref=_source_ref_for_plugin_capability(
                        plugin_name=plugin_name,
                        canonical_id=cmd_name,
                    ),
                    metadata={
                        "plugin_name": plugin_name,
                        "origin": "plugin_command",
                        "manifest": _dump_manifest_payload(pseudo_manifest),
                        # v5 渐进式披露：command 伪 skill 也需要 marketplace 元数据
                        "marketplace_name": getattr(cmd_descriptor, "marketplace_name", None),
                        "marketplace_description": getattr(cmd_descriptor, "marketplace_description", None),
                        "plugin_description": getattr(cmd_descriptor, "plugin_description", None),
                    },
                    projection_targets=("skills",),
                )
            )

    mcp_skill_fingerprints: list[str] = []
    if mcp_skill_manifests_builder is not None:
        try:
            mcp_manifests = list(mcp_skill_manifests_builder())
        except Exception:
            mcp_manifests = []
        for manifest in mcp_manifests:
            name = getattr(manifest, "name", None)
            if not isinstance(name, str) or not name or name in seen_skills:
                continue
            seen_skills.add(name)
            server_name = getattr(manifest, "loaded_from", "") or ""
            mcp_skill_fingerprints.append(
                f"mcp-skill:{server_name}:{name}"
            )
            nodes.append(
                assembler.make_enabled_node(
                    canonical_id=name,
                    kind=CapabilityKind.SKILL,
                    display_name=name,
                    source_ref=_source_ref_for_mcp_skill(server_name),
                    metadata={
                        "plugin_name": None,
                        "origin": "mcp",
                        "manifest": _dump_manifest_payload(manifest),
                    },
                    projection_targets=("skills",),
                )
            )

    manifests: list[Any] = []
    if subagent_registry is not None and hasattr(subagent_registry, "list_all"):
        manifests = list(subagent_registry.list_all())
    agent_manifests = _dedupe_by_name(manifests)
    seen_agents = {getattr(m, "name", None) for m in agent_manifests if isinstance(getattr(m, "name", None), str)}
    extension_agent_fingerprints: list[str] = []

    for manifest in agent_manifests:
        name = getattr(manifest, "name", None)
        if not isinstance(name, str) or not name:
            continue
        nodes.append(
            assembler.make_enabled_node(
                canonical_id=name,
                kind=CapabilityKind.AGENT,
                display_name=name,
                source_ref=_generic_source_ref(
                    source_id=f"workspace-agent:{name}",
                    source_kind=SourceKind.WORKSPACE,
                    trust_level=TrustLevel.MEDIUM,
                    origin_path=None,
                ),
                metadata={
                    "plugin_name": None,
                    "origin": None,
                    "manifest": _dump_manifest_payload(manifest),
                },
                projection_targets=("agents",),
            )
        )

    if extension_manager is not None and hasattr(extension_manager, "iter_agent_descriptors"):
        for descriptor in extension_manager.iter_agent_descriptors():
            manifest = getattr(descriptor, "manifest", None)
            capability_name = getattr(descriptor, "canonical_id", None)
            if not isinstance(capability_name, str) or not capability_name or capability_name in seen_agents:
                continue
            plugin_name = getattr(descriptor, "plugin_name", None)
            extension_agent_fingerprints.append(
                f"plugin-agent:{plugin_name or ''}:{capability_name}:{getattr(manifest, 'source', '') or ''}"
            )
            nodes.append(
                assembler.make_enabled_node(
                    canonical_id=capability_name,
                    kind=CapabilityKind.AGENT,
                    display_name=capability_name,
                    source_ref=_source_ref_for_plugin_capability(
                        plugin_name=plugin_name,
                        canonical_id=capability_name,
                    ),
                    metadata={
                        "plugin_name": plugin_name,
                        "origin": getattr(descriptor, "origin", None),
                        "manifest": _dump_manifest_payload(manifest),
                        # v5 渐进式披露：agent 也需要 marketplace 元数据
                        "marketplace_name": getattr(descriptor, "marketplace_name", None),
                        "marketplace_description": getattr(descriptor, "marketplace_description", None),
                        "plugin_description": getattr(descriptor, "plugin_description", None),
                    },
                    projection_targets=("agents",),
                )
            )

    if tool_registry is not None:
        for tool in tool_registry.list_tools():
            nodes.append(
                assembler.make_enabled_node(
                    canonical_id=tool.name,
                    kind=CapabilityKind.TOOL,
                    display_name=tool.name,
                    source_ref=_source_ref_for_tool(tool),
                    metadata={
                        "tool_class": getattr(tool, "tool_class", None),
                        "effect_class": str(getattr(tool, "effect_class", "unknown_effect")),
                    },
                    projection_targets=("tools",),
                )
            )

    nodes = _dedupe_capability_nodes(nodes)
    source_fingerprint = assembler.fingerprint(
        [
            str(workspace_path or ""),
            *(
                f"skill:{getattr(manifest, 'name', '')}:{getattr(manifest, 'file_path', '')}"
                for manifest in skill_manifests
            ),
            *extension_skill_fingerprints,
            *extension_command_fingerprints,
            *mcp_skill_fingerprints,
            *(
                f"agent:{getattr(manifest, 'name', '')}:{getattr(manifest, 'source', '')}"
                for manifest in agent_manifests
            ),
            *extension_agent_fingerprints,
            *(
                f"tool:{tool.name}:{getattr(tool, 'server_name', '')}"
                for tool in (tool_registry.list_tools() if tool_registry is not None else [])
            ),
        ]
    )
    assembled = assembler.build(
        run_id=f"catalog:{source_fingerprint[:12]}",
        source_fingerprint=source_fingerprint,
        nodes=nodes,
    )
    return assembled.graph, assembled.runtime
