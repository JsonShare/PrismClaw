from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from prismclaw.engine.toolkit.registry.governance import resolve_registry_tool_governance
from prismclaw.engine.toolkit.registry.registry import ToolRegistry
from prismclaw.schema.services.skills import (
    SkillInvocationState,
    normalize_skill_ref,
    resolve_skill_name,
)

if TYPE_CHECKING:
    from prismclaw.engine.experts.scope import ToolSurface
    from prismclaw.engine.toolkit.registry.tool_runtime import Tool
    from prismclaw.schema.services.skills import SkillManifest


@dataclass(frozen=True, slots=True)
class SkillCatalogEntry:
    name: str
    description: str
    location: str
    instructions: str
    manifest: Any
    expert_name: str | None = None
    origin: str | None = None
    expert_description: str | None = None


@dataclass(frozen=True, slots=True)
class SkillResolution:
    """Result of resolving a skill reference against the catalog.

    kind == "resolved": ``entry`` is set. ``expert_name`` is additionally
        set when the match came from the package-alias pass, so callers can
        disclose "matched via package" instead of letting the alias hit look
        like a plain skill-name hit.
    kind == "ambiguous": the reference matched a package whose skills are many;
        ``expert_name`` is that package and ``member_names`` lists its skills.
    kind == "not_found": no skill name or package alias matched.
    """

    kind: Literal["resolved", "ambiguous", "not_found"]
    entry: SkillCatalogEntry | None = None
    expert_name: str | None = None
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
    manifest: Any
    expert_name: str | None = None
    origin: str | None = None
    expert_description: str | None = None


@dataclass(slots=True)
class RuntimeCatalog:
    """Unified runtime catalog.

    This is the listing-grade, runner-scoped fact source for skills, tools, and
    agents. Skill/agent entries are assembled once from runtime registries and
    providers, then passed through the runner instead of re-querying discovery
    sources ad hoc.
    """

    tool_registry: ToolRegistry | None = None
    skill_entries: tuple[SkillCatalogEntry, ...] = field(default_factory=tuple)
    agent_entries: tuple[AgentCatalogEntry, ...] = field(default_factory=tuple)

    def list_skill_entries(self) -> list[SkillCatalogEntry]:
        entries = list(self.skill_entries)
        entries.sort(key=lambda item: (item.name, item.expert_name or ""))
        return entries

    def resolve_skill_reference(
        self,
        skill_name: str,
        *,
        expert_prefer: str | None = None,
    ) -> SkillResolution:
        """Resolve a skill reference through the single resolution contract.

        Pass order is priority: a skill name always wins over a package alias,
        so a package name can never hijack a same-named skill.

        Pass 1 (skill name): exact then case-folded match via ``resolve_skill_name``,
        ``expert_prefer`` picks the version among same-name candidates.
        Pass 1.5 (short name): when the reference is an un-namespaced short token
        (e.g. ``xlsx``), a skill whose canonical name ends with ``-xlsx`` /
        ``:xlsx`` resolves too — but only if exactly one such skill exists.
        This absorbs the common model habit of calling by file type while keeping
        ambiguity an error.
        Pass 2 (package alias): the normalized reference is case-fold-compared
        against each entry's ``expert_name``; a package holding exactly one
        distinct skill name resolves directly to it (carrying the package name for
        disclosure), otherwise the result is ambiguous with every member listed.
        """
        entries = self.list_skill_entries()

        normalized = normalize_skill_ref(skill_name)
        resolved_name = resolve_skill_name(skill_name, entries)
        if resolved_name is not None:
            return SkillResolution(
                kind="resolved",
                entry=self._pick_entry(entries, resolved_name, expert_prefer),
            )

        if normalized and ":" not in normalized and len(normalized) >= 2:
            folded = normalized.lower()
            suffix_hits = sorted(
                {
                    entry.name
                    for entry in entries
                    if entry.name.lower().endswith(("-" + folded, ":" + folded))
                }
            )
            if len(suffix_hits) == 1:
                return SkillResolution(
                    kind="resolved",
                    entry=self._pick_entry(entries, suffix_hits[0], expert_prefer),
                )
            if len(suffix_hits) > 1:
                return SkillResolution(
                    kind="ambiguous",
                    member_names=tuple(suffix_hits),
                )

        if normalized:
            folded = normalized.lower()
            alias_entries = [
                entry
                for entry in entries
                if entry.expert_name and entry.expert_name.lower() == folded
            ]
            if alias_entries:
                member_names = tuple(sorted({entry.name for entry in alias_entries}))
                if len(member_names) == 1:
                    return SkillResolution(
                        kind="resolved",
                        entry=self._pick_entry(
                            alias_entries, member_names[0], alias_entries[0].expert_name
                        ),
                        expert_name=alias_entries[0].expert_name,
                    )
                return SkillResolution(
                    kind="ambiguous",
                    expert_name=alias_entries[0].expert_name,
                    member_names=member_names,
                )

        return SkillResolution(kind="not_found")

    @staticmethod
    def _pick_entry(
        entries: list[SkillCatalogEntry],
        name: str,
        expert_prefer: str | None,
    ) -> SkillCatalogEntry:
        """Pick one unambiguous catalog entry for ``name``.

        A preferred package must contain the named skill. Duplicate names never
        fall back to catalog order: doing so can activate another package's body.
        """
        candidates = [entry for entry in entries if entry.name == name]
        if not candidates:
            raise LookupError(f"Skill {name!r} not found.")
        if len(candidates) == 1:
            return candidates[0]
        if expert_prefer:
            for entry in candidates:
                if entry.expert_name == expert_prefer:
                    return entry
            raise LookupError(
                f"Skill {name!r} is not available in expert {expert_prefer!r}; "
                "refusing to activate another package's version."
            )
        owners = sorted(entry.expert_name or "<project>" for entry in candidates)
        raise LookupError(
            f"Skill {name!r} is ambiguous across experts: {', '.join(owners)}; "
            "expert_prefer is required."
        )

    def get_skill_entry(
        self,
        skill_name: str,
        *,
        expert_prefer: str | None = None,
    ) -> SkillCatalogEntry | None:
        """按名字解析 skill；expert_prefer 用于在同 canonical_id 多版本中
        优先命中归属 expert 的版本。

        委托 resolve_skill_reference：技能名直取，包别名按其规则
        （唯一成员直取 / 多成员视为未命中）解析，非 resolved 一律返回 None。
        """
        resolution = self.resolve_skill_reference(
            skill_name, expert_prefer=expert_prefer
        )
        return resolution.entry if resolution.kind == "resolved" else None

    def resolve_skill_manifest(
        self,
        skill_name: str,
        *,
        expert_prefer: str | None = None,
    ) -> SkillManifest | None:
        entry = self.get_skill_entry(skill_name, expert_prefer=expert_prefer)
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
        visible_names: set[str],
        subagent_depth: int = 0,
    ) -> list[ToolCatalogEntry]:
        """按本轮可见集列举工具条目。

        ``visible_names`` 来自 ``TurnPlan.toolset.visible_tools_for_llm``——
        唯一治理点算出的结果。此处不再重跑 governance：那会是一次重复计算，
        且历史实现漏传 ``allowed_tool_owners``，导致 ``<tools>`` 段列出模型实际
        调不到的工具（SDD D14）。
        """
        if self.tool_registry is None:
            return []
        tools = self.tool_registry.list_tools(
            allowed_names=set(visible_names),
            subagent_depth=subagent_depth,
        )
        return [self._tool_entry_from_tool(tool, subagent_depth=subagent_depth) for tool in tools]

    def list_searchable_tool_entries(
        self,
        *,
        skill_invocation: SkillInvocationState | None = None,
        discovered_tools: set[str] | None = None,
        subagent_depth: int = 0,
        headless: bool = False,
        tool_surface: ToolSurface | None = None,
    ) -> list[ToolCatalogEntry]:
        if self.tool_registry is None:
            return []
        resolved_toolset = resolve_registry_tool_governance(
            self.tool_registry,
            skill_invocation=skill_invocation,
            discovered_tools=discovered_tools,
            subagent_depth=subagent_depth,
            headless=headless,
            tool_surface=tool_surface,
        )
        searchable_names = set(resolved_toolset.searchable_tools)
        tools = self.tool_registry.list_tools(
            allowed_names=searchable_names,
            subagent_depth=subagent_depth,
        )
        return [self._tool_entry_from_tool(tool, subagent_depth=subagent_depth) for tool in tools]

    def list_agent_entries(self) -> list[AgentCatalogEntry]:
        entries = list(self.agent_entries)
        entries.sort(key=lambda item: item.name)
        return entries

    def get_agent_entry(self, agent_name: str) -> AgentCatalogEntry | None:
        for entry in self.agent_entries:
            if entry.name == agent_name:
                return entry
        return None

    def count_agent_entries(self) -> int:
        return len(self.agent_entries)

    def _tool_entry_from_tool(
        self,
        tool: Tool,
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
