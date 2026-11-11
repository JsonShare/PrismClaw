from __future__ import annotations

from pathlib import Path
from typing import Any

from prismclaw.schema.agent.capability import (
    CapabilityKind,
    PluginCapabilityGraph,
    PluginCommandCapability,
    PluginSourceRef,
    ScopeKind,
    SourceKind,
    TrustLevel,
)
from prismclaw.engine.execution.capabilities.graph_assembler import CapabilityAssembler
from prismclaw.engine.plugins.manifest import PluginManifest


def _normalize_commands(commands: list[Any]) -> list[PluginCommandCapability]:
    """把装配阶段原始 command dicts 归一为 PluginCommandCapability。"""
    result: list[PluginCommandCapability] = []
    for command in commands:
        if not isinstance(command, dict):
            continue
        result.append(
            PluginCommandCapability(
                name=str(command.get("name", "")),
                description=str(command.get("description", "")),
                body=str(command.get("body", "")),
                metadata=dict(command),
            )
        )
    return result


def build_plugin_capability_graph(
    *,
    plugin_name: str,
    namespace: str,
    root_path: "Path",
    manifest: PluginManifest,
    hooks_config: dict[str, Any],
    commands: list[Any],
    output_styles: dict[str, str],
    mcp_servers: dict[str, Any],
    skills: list[Any],
    agents: list[Any],
) -> PluginCapabilityGraph:
    """单轨函数：直接从装配阶段的结构化原始参数构建能力图。"""
    command_models = _normalize_commands(commands)
    assembler = CapabilityAssembler()
    source_ref = _plugin_source_ref(plugin_name, namespace, root_path, manifest)
    nodes: list[Any] = []

    for command in command_models:
        nodes.append(
            assembler.make_enabled_node(
                canonical_id=command.name,
                kind=CapabilityKind.COMMAND,
                display_name=command.name,
                source_ref=source_ref,
                metadata={"plugin_name": plugin_name},
                projection_targets=("commands",),
            )
        )

    for skill in skills:
        skill_name = getattr(skill, "name", None)
        if isinstance(skill_name, str) and skill_name:
            canonical_id = _plugin_capability_id(plugin_name, skill_name)
            nodes.append(
                assembler.make_enabled_node(
                    canonical_id=canonical_id,
                    kind=CapabilityKind.SKILL,
                    display_name=skill_name,
                    source_ref=source_ref,
                    metadata={"plugin_name": plugin_name},
                    projection_targets=("skills",),
                )
            )

    for agent in agents:
        agent_name = getattr(agent, "name", None)
        if isinstance(agent_name, str) and agent_name:
            canonical_id = _plugin_capability_id(plugin_name, agent_name)
            nodes.append(
                assembler.make_enabled_node(
                    canonical_id=canonical_id,
                    kind=CapabilityKind.AGENT,
                    display_name=agent_name,
                    source_ref=source_ref,
                    metadata={"plugin_name": plugin_name},
                    projection_targets=("agents",),
                )
            )

    for event_name in hooks_config.keys():
        if not isinstance(event_name, str) or not event_name:
            continue
        nodes.append(
            assembler.make_enabled_node(
                canonical_id=f"{plugin_name}:hook:{event_name}",
                kind=CapabilityKind.HOOK,
                display_name=event_name,
                source_ref=source_ref,
                metadata={"plugin_name": plugin_name},
                projection_targets=("hooks",),
            )
        )

    for server_name in mcp_servers.keys():
        if not isinstance(server_name, str) or not server_name:
            continue
        nodes.append(
            assembler.make_enabled_node(
                canonical_id=f"{plugin_name}.{server_name}",
                kind=CapabilityKind.MCP_SERVER,
                display_name=server_name,
                source_ref=source_ref,
                metadata={"plugin_name": plugin_name},
                projection_targets=("mcp_servers",),
            )
        )

    for style_name in output_styles.keys():
        if not isinstance(style_name, str) or not style_name:
            continue
        nodes.append(
            assembler.make_enabled_node(
                canonical_id=style_name,
                kind=CapabilityKind.OUTPUT_STYLE,
                display_name=style_name,
                source_ref=source_ref,
                metadata={"plugin_name": plugin_name},
                projection_targets=("output_styles",),
            )
        )

    assembled = assembler.build(
        run_id=f"plugin:{plugin_name}",
        source_fingerprint=assembler.fingerprint(
            [
                str(root_path),
                plugin_name,
                namespace,
                *(command.name for command in command_models),
            ]
        ),
        nodes=nodes,
    )
    return PluginCapabilityGraph(
        plugin_name=plugin_name,
        graph=assembled.graph,
        runtime_capabilities=assembled.runtime,
        command_capabilities=command_models,
        skill_capabilities=list(skills),
        agent_capabilities=list(agents),
        hook_capabilities=dict(hooks_config),
        mcp_capabilities=dict(mcp_servers),
        output_style_capabilities=dict(output_styles),
    )


def _plugin_source_ref(
    plugin_name: str,
    namespace: str,
    root_path: "Path",
    manifest: PluginManifest,
) -> PluginSourceRef:
    source_kind = SourceKind.LOCAL_PLUGIN
    managed_ref: str | None = None
    if namespace.startswith("session:"):
        source_kind = SourceKind.SESSION_PLUGIN
    elif namespace.startswith("managed:"):
        source_kind = SourceKind.MANAGED_PLUGIN
        managed_ref = namespace
    return PluginSourceRef(
        source_id=f"plugin:{plugin_name}",
        source_kind=source_kind,
        trust_level=TrustLevel.MEDIUM,
        scope_kind=ScopeKind.WORKSPACE,
        scope_id=str(_safe_path(root_path)),
        version=str(manifest.version) if manifest.version is not None else None,
        origin_path=str(root_path),
        managed_ref=managed_ref,
    )


def _plugin_capability_id(plugin_name: str, raw_name: str) -> str:
    if raw_name.startswith(f"{plugin_name}:") or raw_name.startswith(f"/{plugin_name}:"):
        return raw_name
    return f"{plugin_name}:{raw_name}"


def _safe_path(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path
