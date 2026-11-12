from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from prismclaw.schema.agent.capability import (
    CapabilityGraph,
    CapabilityKind,
    RuntimeCapabilities,
)
from prismclaw.engine.toolkit.registry.governance import resolve_registry_tool_governance
from prismclaw.engine.toolkit.registry.registry import ToolRegistry
from prismclaw.schema.services.skills import SkillInvocationState
from prismclaw.schema.services.skills import normalize_skill_ref, resolve_skill_name
from prismclaw.schema.agent.types import WorkspaceMode


@dataclass(frozen=True, slots=True)
class SkillCatalogEntry:
    name: str
    description: str
    location: str
    instructions: str
    manifest: Any
    plugin_name: str | None = None
    origin: str | None = None
    # v5 渐进式披露：所属 marketplace 名称（如 medical-healthcare）
    marketplace_name: str | None = None
    # v5 渐进式披露：marketplace / 插件级描述（来自市场级 plugin.json 的 description）
    marketplace_description: str | None = None
    # 子插件级描述（来自子插件自身 manifest description）
    plugin_description: str | None = None


@dataclass(frozen=True, slots=True)
class SkillResolution:
    """Result of resolving a skill reference against the catalog.

    kind == "resolved": ``entry`` is set. ``marketplace_name`` is additionally
        set when the match came from the marketplace-alias pass, so callers
        can disclose "matched via package" instead of letting the alias hit
        look like a plain skill-name hit.
    kind == "ambiguous": the reference matched a marketplace package whose
        skills are many; ``marketplace_name`` is that package and
        ``member_names`` lists every skill under it.
    kind == "not_found": no skill name or marketplace alias matched.
    """

    kind: Literal["resolved", "ambiguous", "not_found"]
    entry: SkillCatalogEntry | None = None
    marketplace_name: str | None = None
    member_names: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ToolCatalogEntry:
    canonical_name: str
    llm_name: str
    description: str
    tool_class: str
    effect_class: str
    deferred: bool = False
    parent_block_reason: str | None = None


@dataclass(frozen=True, slots=True)
class AgentCatalogEntry:
    name: str
    description: str
    can_handoff_to: tuple[str, ...]
    manifest: Any
    plugin_name: str | None = None
    origin: str | None = None
    marketplace_name: str | None = None
    marketplace_description: str | None = None


@dataclass(slots=True)
class CapabilityCatalogService:
    """Unified runtime catalog.

    This is the listing-grade, runner-scoped fact source for skills, tools, and
    agents. It is built once from runtime registries/providers, then passed
    through the runner instead of re-querying discovery sources ad hoc.
    """

    tool_registry: ToolRegistry | None = None
    capability_graph: CapabilityGraph | None = None
    runtime_capabilities: RuntimeCapabilities | None = None

    def list_skill_entries(self, workspace_path: str | Path | None = None) -> list[SkillCatalogEntry]:
        _ = workspace_path
        graph = self.capability_graph
        if graph is None:
            return []
        # runtime_capabilities.skills 若已声明,仅用作启用过滤器(同名 canonical_id 的所有 plugin 版本视为一体启用)。
        # 否则直接从 enabled_nodes() 拿所有 SKILL 类型。
        if self.runtime_capabilities is not None:
            enabled_id_set = set(self.runtime_capabilities.skills)
        else:
            enabled_id_set = None
        # 去重键 = (canonical_id, plugin_name),避免重复来源注册同一份
        seen: set[tuple[str, str | None]] = set()
        entries: list[SkillCatalogEntry] = []
        from prismclaw.schema.services.skills import SkillManifest
        for node in graph.enabled_nodes():
            if node.kind != CapabilityKind.SKILL:
                continue
            if enabled_id_set is not None and node.canonical_id not in enabled_id_set:
                continue
            plugin_name = (
                node.metadata.get("plugin_name")
                if isinstance(node.metadata.get("plugin_name"), str)
                else None
            )
            key = (node.canonical_id, plugin_name)
            if key in seen:
                continue
            seen.add(key)
            payload = node.metadata.get("manifest")
            if not isinstance(payload, dict):
                raise RuntimeError(
                    f"CapabilityCatalogService: missing skill manifest payload for {node.canonical_id!r}"
                )
            manifest = SkillManifest.model_validate(payload)
            entries.append(
                SkillCatalogEntry(
                    name=node.canonical_id,
                    description=manifest.description or "",
                    location=manifest.file_path or "",
                    instructions=manifest.body or "",
                    manifest=manifest,
                    plugin_name=plugin_name,
                    origin=node.metadata.get("origin")
                    if isinstance(node.metadata.get("origin"), str)
                    else None,
                    marketplace_name=node.metadata.get("marketplace_name")
                    if isinstance(node.metadata.get("marketplace_name"), str)
                    else None,
                    marketplace_description=node.metadata.get("marketplace_description")
                    if isinstance(node.metadata.get("marketplace_description"), str)
                    else None,
                    plugin_description=node.metadata.get("plugin_description")
                    if isinstance(node.metadata.get("plugin_description"), str)
                    else None,
                )
            )
        entries.sort(key=lambda item: (item.name, item.plugin_name or ""))
        return entries

    def resolve_skill_reference(
        self, skill_name: str, *, plugin_prefer: str | None = None
    ) -> SkillResolution:
        """Resolve a skill reference through the single resolution contract.

        Pass order is priority: a skill name always wins over a marketplace
        alias, so a package name can never hijack a same-named skill.

        Pass 1 (skill name): exact then case-folded match via ``resolve_skill_name``,
        ``plugin_prefer`` picks the version among same-name candidates.
        Pass 2 (marketplace alias): the normalized reference is case-fold-compared
        against each entry's ``marketplace_name``; a package holding exactly one
        distinct skill name resolves directly to it (carrying the package name for
        disclosure), otherwise the result is ambiguous with every member listed.
        """
        entries = self.list_skill_entries()

        resolved_name = resolve_skill_name(skill_name, entries)
        if resolved_name is not None:
            return SkillResolution(
                kind="resolved",
                entry=self._pick_entry(entries, resolved_name, plugin_prefer),
            )

        normalized = normalize_skill_ref(skill_name)
        if normalized:
            folded = normalized.lower()
            alias_entries = [
                entry
                for entry in entries
                if entry.marketplace_name and entry.marketplace_name.lower() == folded
            ]
            if alias_entries:
                member_names = tuple(sorted({entry.name for entry in alias_entries}))
                if len(member_names) == 1:
                    return SkillResolution(
                        kind="resolved",
                        entry=self._pick_entry(entries, member_names[0], plugin_prefer),
                        marketplace_name=alias_entries[0].marketplace_name,
                    )
                return SkillResolution(
                    kind="ambiguous",
                    marketplace_name=alias_entries[0].marketplace_name,
                    member_names=member_names,
                )

        return SkillResolution(kind="not_found")

    @staticmethod
    def _pick_entry(
        entries: list[SkillCatalogEntry],
        name: str,
        plugin_prefer: str | None,
    ) -> SkillCatalogEntry:
        """Pick the catalog entry for ``name``; ``plugin_prefer`` wins over version order.

        Precondition: at least one entry in ``entries`` has this ``name`` (both
        call sites guarantee it — the name comes from those same entries).
        """
        candidates = [entry for entry in entries if entry.name == name]
        if plugin_prefer:
            for entry in candidates:
                if entry.plugin_name == plugin_prefer:
                    return entry
        return candidates[0]

    def get_skill_entry(
        self, skill_name: str, *, plugin_prefer: str | None = None
    ) -> SkillCatalogEntry | None:
        """按名字解析 skill,支持按 plugin_prefer 在同 canonical_id 多版本中优先命中归属 plugin 的版本。

        委托 resolve_skill_reference：技能名直取，marketplace 别名按其规则
        （唯一成员直取 / 多成员视为未命中）解析，非 resolved 一律返回 None。
        """
        resolution = self.resolve_skill_reference(skill_name, plugin_prefer=plugin_prefer)
        return resolution.entry if resolution.kind == "resolved" else None

    def resolve_skill_manifest(
        self, skill_name: str, *, plugin_prefer: str | None = None
    ) -> Any | None:
        entry = self.get_skill_entry(skill_name, plugin_prefer=plugin_prefer)
        return entry.manifest if entry is not None else None

    def list_tool_entries(
        self,
        *,
        subagent_depth: int = 0,
    ) -> list[ToolCatalogEntry]:
        if self.tool_registry is None:
            return []
        tools = self.tool_registry.list_tools(
            allowed_names=None,
            subagent_depth=subagent_depth,
        )
        return [self._tool_entry_from_tool(tool, subagent_depth=subagent_depth) for tool in tools]

    def list_visible_tool_entries(
        self,
        *,
        skill_invocation: SkillInvocationState | None = None,
        discovered_tools: set[str] | None = None,
        mode: WorkspaceMode = "work",
        subagent_depth: int = 0,
        headless: bool = False,
    ) -> list[ToolCatalogEntry]:
        if self.tool_registry is None:
            return []
        resolved_toolset = resolve_registry_tool_governance(
            self.tool_registry,
            skill_invocation=skill_invocation,
            discovered_tools=discovered_tools,
            mode=mode,
            subagent_depth=subagent_depth,
            headless=headless,
        )
        allowed_names = set(resolved_toolset.visible_tools_for_llm)
        tools = self.tool_registry.list_tools(
            allowed_names=allowed_names,
            subagent_depth=subagent_depth,
        )
        return [self._tool_entry_from_tool(tool, subagent_depth=subagent_depth) for tool in tools]

    def list_searchable_tool_entries(
        self,
        *,
        skill_invocation: SkillInvocationState | None = None,
        discovered_tools: set[str] | None = None,
        mode: WorkspaceMode = "work",
        subagent_depth: int = 0,
        headless: bool = False,
    ) -> list[ToolCatalogEntry]:
        if self.tool_registry is None:
            return []
        resolved_toolset = resolve_registry_tool_governance(
            self.tool_registry,
            skill_invocation=skill_invocation,
            discovered_tools=discovered_tools,
            mode=mode,
            subagent_depth=subagent_depth,
            headless=headless,
        )
        searchable_names = set(resolved_toolset.searchable_tools)
        tools = self.tool_registry.list_tools(
            allowed_names=searchable_names,
            subagent_depth=subagent_depth,
        )
        return [self._tool_entry_from_tool(tool, subagent_depth=subagent_depth) for tool in tools]

    def list_agent_entries(self) -> list[AgentCatalogEntry]:
        graph = self.capability_graph
        if graph is None:
            return []
        enabled_ids: tuple[str, ...] = ()
        if self.runtime_capabilities is not None:
            enabled_ids = self.runtime_capabilities.agents
        else:
            enabled_ids = tuple(
                node.canonical_id
                for node in graph.enabled_nodes()
                if node.kind == CapabilityKind.AGENT
            )
        entries: list[AgentCatalogEntry] = []
        from prismclaw.engine.subagent.definition.manifest import AgentManifest
        for canonical_id in enabled_ids:
            node = graph.get(canonical_id)
            if node is None or node.kind != CapabilityKind.AGENT:
                continue
            payload = node.metadata.get("manifest")
            if not isinstance(payload, dict):
                raise RuntimeError(
                    f"CapabilityCatalogService: missing agent manifest payload for {canonical_id!r}"
                )
            manifest = AgentManifest.model_validate(payload)
            entries.append(
                AgentCatalogEntry(
                    name=canonical_id,
                    description=manifest.description or "",
                    can_handoff_to=tuple(manifest.can_handoff_to or ()),
                    manifest=manifest,
                    plugin_name=node.metadata.get("plugin_name")
                    if isinstance(node.metadata.get("plugin_name"), str)
                    else None,
                    origin=node.metadata.get("origin")
                    if isinstance(node.metadata.get("origin"), str)
                    else None,
                    marketplace_name=node.metadata.get("marketplace_name")
                    if isinstance(node.metadata.get("marketplace_name"), str)
                    else None,
                    marketplace_description=node.metadata.get("marketplace_description")
                    if isinstance(node.metadata.get("marketplace_description"), str)
                    else None,
                )
            )
        entries.sort(key=lambda item: item.name)
        return entries

    def get_agent_entry(self, agent_name: str) -> AgentCatalogEntry | None:
        for entry in self.list_agent_entries():
            if entry.name == agent_name:
                return entry
        return None

    def count_agent_entries(self) -> int:
        return len(self.list_agent_entries())

    def group_agent_entries_by_plugin(self) -> dict[str, list[AgentCatalogEntry]]:
        grouped: dict[str, list[AgentCatalogEntry]] = {}
        for entry in self.list_agent_entries():
            grouped.setdefault(entry.plugin_name or "unknown", []).append(entry)
        return grouped

    def _tool_entry_from_tool(
        self,
        tool: Any,
        *,
        subagent_depth: int,
    ) -> ToolCatalogEntry:
        parent_block_reason = None
        raw_scope = getattr(tool, "visibility_scope", "all")
        parent_only = bool(getattr(tool, "parent_only", False))
        if subagent_depth <= 0 and raw_scope == "subagent_only":
            parent_block_reason = "tool_hidden_for_main_agent"
        elif subagent_depth > 0 and (raw_scope == "main_only" or parent_only):
            parent_block_reason = "tool_hidden_for_subagent"
        return ToolCatalogEntry(
            canonical_name=tool.name,
            llm_name=ToolRegistry.to_llm_name(tool.name),
            description=getattr(tool, "description", "") or "",
            tool_class=getattr(tool, "tool_class", "write"),
            effect_class=str(getattr(tool, "effect_class", "unknown_effect")),
            deferred=bool(getattr(tool, "deferred", False)),
            parent_block_reason=parent_block_reason,
        )
