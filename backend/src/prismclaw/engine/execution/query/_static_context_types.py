from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from prismclaw.engine.execution.loop.deps import AgentRunnerDeps
    from prismclaw.schema.agent.types import RunRequest
    from prismclaw.schema.session import SessionTree

from prismclaw.engine.execution.capabilities.catalog import (
    CapabilityCatalogService,
    CapabilityKind,
)
from prismclaw.engine.execution.request._prompt_types import (
    AvailableSubagentEntry,
    MarketplaceSummary,
    SystemPromptResult,
)
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
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],  # visible_tool_names：<tools> 段的直接数据源
    str,  # skill_catalog_fingerprint
    str,  # subagent_catalog_fingerprint
]


@dataclass
class StaticContextResult:
    prompt_inputs: Any  # PromptInputs
    frozen_result: SystemPromptResult
    history_budget: int
    prior_replacement_state: ReplacementDecisionState | None
    shell_snapshot: StaticTurnShellSnapshot | None = None


@dataclass(frozen=True, slots=True)
class StaticTurnShellSnapshot:
    workspace_id: str
    workspace_path: str | None
    model: str
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
    """静态壳缓存：键为 workspace 事实，不随决策漂移，条目数受 LRU 约束。

    会话内 ``plugin_generation`` / ``discovered_tools`` / ``subagent_depth`` /
    ``skill_invocation`` 都会变化，每次都产生新条目。键可以合法地多，
    但生命周期必须有界——否则一个长会话会无限堆积整段 system 前缀。
    """

    # 覆盖单会话内的合法键变动次数（发现累积 × 插件代际 × 技能激活 × 子代理层级），
    # 超出即淘汰最久未用条目。
    MAX_ENTRIES = 32

    def __init__(self, max_entries: int = MAX_ENTRIES) -> None:
        self._max_entries = max_entries
        self._entries: dict[ShellCacheKey, StaticTurnShellSnapshot] = {}

    @property
    def entries(self) -> dict[ShellCacheKey, StaticTurnShellSnapshot]:
        return self._entries

    def get(self, key: ShellCacheKey) -> StaticTurnShellSnapshot | None:
        snapshot = self._entries.get(key)
        if snapshot is not None:
            # 命中即续期：dict 保序，移到末尾即最新。
            self._entries.pop(key)
            self._entries[key] = snapshot
        return snapshot

    def put(self, key: ShellCacheKey, snapshot: StaticTurnShellSnapshot) -> None:
        self._entries.pop(key, None)
        self._entries[key] = snapshot
        while len(self._entries) > self._max_entries:
            oldest = next(iter(self._entries))
            self._entries.pop(oldest)
            logger.debug("static_shell_cache.evict oldest_key=%s", oldest[:4])

    def clear(self) -> None:
        self._entries.clear()


def _load_prior_replacement_state(tree: SessionTree) -> ReplacementDecisionState | None:
    try:
        engine: SessionProjectionEngine = SessionProjectionEngine(tree)
        state = engine.replacement.get_state()
    except Exception:
        logger.warning("get_replacement_decision_state failed", exc_info=True)
        return None
    return state if state.decisions else None


def _resolve_static_shell_cache(deps: AgentRunnerDeps) -> StaticTurnShellCache:
    cache = getattr(deps, "static_shell_cache", None)
    if isinstance(cache, StaticTurnShellCache):
        return cache
    raise RuntimeError("AgentRunnerDeps.static_shell_cache is required for prompt planning")


def _plugin_generation(deps: AgentRunnerDeps) -> int:
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
    request: RunRequest,
    tool_workspace_path: str | None,
    effective_model: str,
    deps: AgentRunnerDeps,
    skill_invocation: SkillInvocationState | None,
    discovered_tools: set[str] | None,
    subagent_depth: int,
    visible_tool_names: set[str],
    skill_catalog_fingerprint: str = "",
    subagent_catalog_fingerprint: str = "",
) -> ShellCacheKey:
    workspace_path = str(tool_workspace_path) if tool_workspace_path else ""
    active_skill_name = skill_invocation.skill_name if skill_invocation else None
    allowed_tools = tuple(skill_invocation.allowed_tools_override or ()) if skill_invocation else ()
    discovered_names = tuple(sorted(discovered_tools or ()))
    # <tools> 段直接由本轮可见集渲染（SDD D14），因此可见集是壳内容的
    # 事实输入，必须进键——否则同一会话内插件作用域 / 熔断 / 技能解锁
    # 改变可见集后会命中旧壳，提示词与实际工具面再次不一致。
    visible_names = tuple(sorted(visible_tool_names))
    return (
        str(request.session_id),
        str(request.workspace_id),
        workspace_path,
        effective_model,
        _plugin_generation(deps),
        subagent_depth,
        (active_skill_name or "", *allowed_tools),
        discovered_names,
        visible_names,
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
