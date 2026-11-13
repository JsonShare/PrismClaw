from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from prismclaw.schema.session import SessionTree

from prismclaw.engine.execution.capabilities.catalog import (
    CapabilityCatalogService,
    CapabilityKind,
)
from prismclaw.engine.execution.request.system_prompt import (
    AvailableSubagentEntry,
    MarketplaceSummary,
    SystemPromptResult,
)
from prismclaw.schema.agent.types import WorkspaceMode
from prismclaw.schema.content.replacement_state import ReplacementDecisionState
from prismclaw.schema.services.skills import SkillInvocationState
from prismclaw.schema.session.projection import SessionProjectionEngine

logger = logging.getLogger(__name__)

ShellCacheKey = tuple[
    str,
    str,
    str,
    str,
    int,
    int,
    str | None,
    str,
    tuple[str, ...],
    tuple[str, ...],
    str,  # skill_catalog_fingerprint
    str,  # subagent_catalog_fingerprint
]


@dataclass
class StaticContextResult:
    prompt_inputs: Any  # PromptInputs
    frozen_result: SystemPromptResult
    history_budget: int
    request_mode: str | None
    prior_replacement_state: ReplacementDecisionState | None
    shell_snapshot: "StaticTurnShellSnapshot | None" = None


@dataclass(frozen=True, slots=True)
class StaticTurnShellSnapshot:
    workspace_id: str
    workspace_path: str | None
    model: str
    request_mode: str | None
    tool_summaries: tuple[tuple[str, str], ...]
    subagent_registry_size: int
    handoff_enabled: bool
    any_manifest_has_can_handoff_to: bool
    max_handoff_chain_length: int
    available_skills: tuple[str, ...]
    available_subagents: tuple[AvailableSubagentEntry, ...]
    frozen_result: SystemPromptResult
    history_budget: int
    # 插件包索引：`<packages>` 的数据源
    marketplace_summaries: tuple[MarketplaceSummary, ...] = ()
    # SDD R-A2: measured tool-schema wire cost already deducted from
    # history_budget; carried on the snapshot so the drift sentinel
    # (log_turn_token_usage) can account for it.
    tools_wire_tokens: int = 0


class StaticTurnShellCache:
    def __init__(self) -> None:
        self._entries: dict[ShellCacheKey, StaticTurnShellSnapshot] = {}

    @property
    def entries(self) -> dict[ShellCacheKey, StaticTurnShellSnapshot]:
        return self._entries

    def get(self, key: ShellCacheKey) -> StaticTurnShellSnapshot | None:
        return self._entries.get(key)

    def put(self, key: ShellCacheKey, snapshot: StaticTurnShellSnapshot) -> None:
        self._entries[key] = snapshot

    def clear(self) -> None:
        self._entries.clear()


def _load_prior_replacement_state(tree: "SessionTree") -> ReplacementDecisionState | None:
    try:
        engine: SessionProjectionEngine = SessionProjectionEngine(tree)
        state = engine.replacement.get_state()
    except Exception:
        logger.warning("get_replacement_decision_state failed", exc_info=True)
        return None
    return state if state.decisions else None


def _resolve_static_shell_cache(deps: Any) -> StaticTurnShellCache:
    cache = getattr(deps, "static_shell_cache", None)
    if isinstance(cache, StaticTurnShellCache):
        return cache
    raise RuntimeError("AgentRunnerDeps.static_shell_cache is required for prompt planning")


def _plugin_generation(deps: Any) -> int:
    lifecycle = getattr(deps.plugin, "lifecycle_manager", None)
    generation = getattr(lifecycle, "generation", 0)
    return generation if isinstance(generation, int) else 0


def _skill_catalog_fingerprint(catalog: CapabilityCatalogService | None) -> str:
    """Stable fingerprint of the enabled skill set.

    The full skill catalog lives in the frozen (cached) system-prompt prefix, so
    the cache key must break when the available skill set changes (install /
    remove / enable / disable), otherwise the model would keep seeing a stale
    catalog. Derived from canonical ids only — no manifest validation per turn.
    """
    if catalog is None:
        return ""
    rc = getattr(catalog, "runtime_capabilities", None)
    if rc is not None and getattr(rc, "skills", None):
        ids = tuple(sorted(rc.skills))
    else:
        graph = getattr(catalog, "capability_graph", None)
        if graph is None:
            return ""
        ids = tuple(
            sorted(
                node.canonical_id
                for node in graph.enabled_nodes()
                if node.kind == CapabilityKind.SKILL
            )
        )
    if not ids:
        return ""
    return hashlib.md5("|".join(ids).encode("utf-8")).hexdigest()[:16]


def _subagent_catalog_fingerprint(catalog: CapabilityCatalogService | None) -> str:
    """Stable fingerprint of the enabled subagent set.

    Same rationale as _skill_catalog_fingerprint: the frozen prefix contains
    the <packages> index and the core <subagents> block, so cache keys must
    break on any install/remove/enable/disable. Derived from canonical ids only.
    """
    if catalog is None:
        return ""
    rc = getattr(catalog, "runtime_capabilities", None)
    if rc is not None and getattr(rc, "agents", None):
        ids = tuple(sorted(rc.agents))
    else:
        graph = getattr(catalog, "capability_graph", None)
        if graph is None:
            return ""
        ids = tuple(
            sorted(
                node.canonical_id
                for node in graph.enabled_nodes()
                if node.kind == CapabilityKind.AGENT
            )
        )
    if not ids:
        return ""
    return hashlib.md5("|".join(ids).encode("utf-8")).hexdigest()[:16]


def _shell_cache_key(
    *,
    request: Any,
    tool_workspace_path: str | None,
    effective_model: str,
    request_mode: str | None,
    mode: WorkspaceMode,
    deps: Any,
    skill_invocation: SkillInvocationState | None,
    discovered_tools: set[str] | None,
    subagent_depth: int,
    skill_catalog_fingerprint: str = "",
    subagent_catalog_fingerprint: str = "",
) -> ShellCacheKey:
    workspace_path = str(tool_workspace_path) if tool_workspace_path else ""
    active_skill_name = skill_invocation.skill_name if skill_invocation else None
    allowed_tools = tuple(skill_invocation.allowed_tools_override or ()) if skill_invocation else ()
    discovered_names = tuple(sorted(discovered_tools or ()))
    return (
        str(request.session_id),
        str(request.workspace_id),
        workspace_path,
        effective_model,
        _plugin_generation(deps),
        subagent_depth,
        request_mode or "",
        str(mode),
        (active_skill_name or "", *allowed_tools),
        discovered_names,
        skill_catalog_fingerprint,
        subagent_catalog_fingerprint,
    )


# SDD D11：L1 注入上限——<memory_index> 是索引面不是全量面，超限部分用
# trailer 渐进披露（模型可经 memory 工具检索）。
MAX_L1_SNAPSHOT_LINES = 15


def format_l1_snapshot(entries: list[Any]) -> str:
    if not entries:
        return ""
    active = []
    for entry in entries:
        status = getattr(entry, "status", "active")
        if isinstance(entry, dict):
            status = entry.get("status", "active")
        if status == "active":
            content = getattr(entry, "content", None)
            if content is None and isinstance(entry, dict):
                content = entry.get("content", "")
            if content:
                active.append(content)
    if not active:
        return ""
    lines = ["<memory_index>"]
    hidden = len(active) - MAX_L1_SNAPSHOT_LINES
    for content in active[:MAX_L1_SNAPSHOT_LINES]:
        lines.append(f"- {content}")
    if hidden > 0:
        lines.append(f"- …及 {hidden} 条未显示（用 memory 工具检索）")
    lines.append("</memory_index>")
    return "\n".join(lines)
