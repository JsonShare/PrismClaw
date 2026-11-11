from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from typing import Any, Protocol, Sequence

from pydantic import BaseModel, Field

from prismclaw.engine.toolkit.registry.tool_runtime import Tool
from prismclaw.engine.toolkit.registry.registry import ToolRegistry
from prismclaw.engine.security.gate.types import (
    ActorIdentity,
    AllowedToolsPolicy,
    RuntimeMode,
)
from prismclaw.engine.toolkit.registry.specs import (
    ToolExecutionTraits,
    ToolPermissionTraits,
    ToolSpec,
    VisibilityScope,
)
from prismclaw.schema.agent.types import WorkspaceMode
from prismclaw.schema.services.skills import SkillInvocationState

logger = logging.getLogger(__name__)


# 通用工具面：不受任何 skill 的 allowed_tools 白名单限制，始终对模型可见可调用。
#   - present_files：交付物的可选声明通道（表现层还会扫描 outputs/ 兜底），
#     任何产出文件的 skill 都应能调用它，但不能调用不该导致任务无法完成。
#   - ask_user_question：人机澄清，skill 经常需要向用户确认。
#   - tool_search / skill_search / activate_skill：技能发现与激活，是 skill
#     生态的入口，被白名单屏蔽会导致无法切换/发现技能。
_UNIVERSAL_TOOLS = frozenset({
    "present_files",
    "ask_user_question",
    "tool_search",
    "skill_search",
    "activate_skill",
})

# 核心可见工具集：flag ``tools.core_visible_only`` 开启时，
# 只有这些工具默认对模型可见；其余工具被 DeferredToolReducer 视同
# requires_discovery——不可见不可直调，但仍可被 tool_search 检索，发现后下一轮解锁。
# 成员须同时覆盖 _UNIVERSAL_TOOLS（发现/交付/澄清通道不应有摩擦）。
_CORE_VISIBLE_TOOLS = frozenset({
    "read",
    "write",
    "edit",
    "bash",
    "ls",
    "grep",
    "glob",
    "todo_write",
    "ask_user_question",
    "tool_search",
    "skill_search",
    "activate_skill",
    "present_files",
})


class DecisionTraceEntry(BaseModel):
    tool_name: str
    reducer: str
    action: str
    reason: str
    details: dict[str, Any] = Field(default_factory=dict)


class ResolvedToolRecord(BaseModel):
    spec: ToolSpec
    visible_to_llm: bool = True
    callable: bool = True
    searchable: bool = True


class ResolvedToolSet(BaseModel):
    all_resolved_tools: dict[str, ResolvedToolRecord]
    visible_tools_for_llm: tuple[str, ...]
    callable_tools: tuple[str, ...]
    denied_tools: tuple[str, ...]
    searchable_tools: tuple[str, ...]
    decision_trace: tuple[DecisionTraceEntry, ...] = ()


@dataclass(frozen=True)
class GovernanceInput:
    catalog: Sequence[ToolSpec]
    actor_identity: ActorIdentity = "main"
    allowed_tools_policy: AllowedToolsPolicy = field(default_factory=AllowedToolsPolicy)
    runtime_mode: RuntimeMode = field(default_factory=RuntimeMode)
    active_skill_name: str | None = None
    mode: str = "work"
    discovered_tools: frozenset[str] = frozenset()
    mcp_available_servers: frozenset[str] = frozenset()
    skill_allowed_tools: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ResolvedToolSetDraft:
    tools: dict[str, ResolvedToolRecord]
    decision_trace: tuple[DecisionTraceEntry, ...] = ()

    def append_trace(self, entry: DecisionTraceEntry) -> "ResolvedToolSetDraft":
        return replace(self, decision_trace=(*self.decision_trace, entry))

    def with_tool(self, tool_name: str, record: ResolvedToolRecord) -> "ResolvedToolSetDraft":
        updated = dict(self.tools)
        updated[tool_name] = record
        return replace(self, tools=updated)


class ToolGovernanceReducer(Protocol):
    name: str

    def apply(
        self,
        input_data: GovernanceInput,
        draft: ResolvedToolSetDraft,
    ) -> ResolvedToolSetDraft: ...


class AllowedToolsPolicyReducer:
    name = "AllowedToolsPolicyReducer"

    def apply(
        self,
        input_data: GovernanceInput,
        draft: ResolvedToolSetDraft,
    ) -> ResolvedToolSetDraft:
        next_draft = draft
        for tool_name, record in draft.tools.items():
            # 通用工具始终放行：它们是平台级能力（交付物声明、人机交互、
            # 技能发现），不应被 skill 的 allowed_tools 白名单屏蔽。
            if tool_name in _UNIVERSAL_TOOLS:
                continue
            if input_data.allowed_tools_policy.allows(tool_name):
                continue
            updated = record.model_copy(update={"visible_to_llm": False, "callable": False, "searchable": False})
            next_draft = next_draft.with_tool(tool_name, updated).append_trace(
                DecisionTraceEntry(
                    tool_name=tool_name,
                    reducer=self.name,
                    action="deny",
                    reason="tool_not_allowed_by_policy",
                )
            )
        return next_draft


class ActorIdentityReducer:
    name = "ActorIdentityReducer"

    def apply(
        self,
        input_data: GovernanceInput,
        draft: ResolvedToolSetDraft,
    ) -> ResolvedToolSetDraft:
        next_draft = draft
        for tool_name, record in draft.tools.items():
            traits = record.spec.permission_traits
            hidden_reason: str | None = None
            if input_data.actor_identity == "subagent":
                if traits.visibility_scope == "main_only" or traits.parent_only:
                    hidden_reason = "tool_hidden_for_subagent"
            elif input_data.actor_identity == "main":
                if traits.visibility_scope == "subagent_only":
                    hidden_reason = "tool_hidden_for_main_agent"
            if hidden_reason is None:
                continue
            updated = record.model_copy(
                update={"visible_to_llm": False, "callable": False, "searchable": False}
            )
            next_draft = next_draft.with_tool(tool_name, updated).append_trace(
                DecisionTraceEntry(
                    tool_name=tool_name,
                    reducer=self.name,
                    action="hide",
                    reason=hidden_reason,
                    details={
                        "actor_identity": input_data.actor_identity,
                        "visibility_scope": traits.visibility_scope,
                        "parent_only": traits.parent_only,
                    },
                )
            )
        return next_draft


class ReadOnlyPermissionReducer:
    name = "ReadOnlyPermissionReducer"

    def apply(
        self,
        input_data: GovernanceInput,
        draft: ResolvedToolSetDraft,
    ) -> ResolvedToolSetDraft:
        next_draft = draft
        for tool_name, record in draft.tools.items():
            traits = record.spec.permission_traits
            enforce_read_only = (
                input_data.mode == "chat"
                or traits.forced_chat_only
            )
            if not enforce_read_only:
                continue
            if traits.read_only_allowed:
                continue
            updated = record.model_copy(
                update={"visible_to_llm": False, "callable": False, "searchable": False}
            )
            next_draft = next_draft.with_tool(tool_name, updated).append_trace(
                DecisionTraceEntry(
                    tool_name=tool_name,
                    reducer=self.name,
                    action="deny",
                    reason=(
                        "tool_forced_chat_only_blocks_write"
                        if traits.forced_chat_only
                        and input_data.mode != "chat"
                        else "tool_not_allowed_in_read_only_mode"
                    ),
                    details={
                        "tool_class": traits.tool_class,
                        "forced_chat_only": traits.forced_chat_only,
                    },
                )
            )
        return next_draft


class HeadlessModeReducer:
    """无头执行时摘除「需要交互用户」的工具。

    判据是能力而非渠道标签：只有持有会话队列、能真正暂停等人回答的执行体
    （当前为 Web 主循环，含复用它的定时任务）才算可交互。CLI / 测试与子代理
    无队列，若仍向模型宣传 AskUserQuestion，模型会调用一个只会返回「已提问、
    等待回答」却根本不会等人的工具——那是谎报，比不提供更糟。
    注：定时任务界面未打开时实际无人应答，这是「与主链路一致」的既定代价。
    """

    name = "HeadlessModeReducer"

    def apply(
        self,
        input_data: GovernanceInput,
        draft: ResolvedToolSetDraft,
    ) -> ResolvedToolSetDraft:
        if not input_data.runtime_mode.headless_mode:
            return draft
        next_draft = draft
        for tool_name, record in draft.tools.items():
            if not record.spec.permission_traits.requires_interactive_user:
                continue
            updated = record.model_copy(
                update={"visible_to_llm": False, "callable": False, "searchable": False}
            )
            next_draft = next_draft.with_tool(tool_name, updated).append_trace(
                DecisionTraceEntry(
                    tool_name=tool_name,
                    reducer=self.name,
                    action="hide",
                    reason="tool_requires_interactive_user",
                    details={"headless_mode": True},
                )
            )
        return next_draft


class DeferredToolReducer:
    """延迟工具在「被发现」之前只隐藏直调能力，但不隐藏可搜索性。

    deferred 的语义是「先发现、后调用」：工具必须能通过 tool_search 被检索到，
    模型才有机会发现它并在下一轮获得直调权限。若同时把 searchable 置 False，
    工具既不可见又搜不到，就永远无法被发现——deferred 变成永久不可达。
    """

    name = "DeferredToolReducer"

    def apply(
        self,
        input_data: GovernanceInput,
        draft: ResolvedToolSetDraft,
    ) -> ResolvedToolSetDraft:
        from prismclaw.engine.observability.feature_flags import (
            FLAG_CORE_VISIBLE_TOOLS,
            is_enabled,
        )

        core_visible_only = is_enabled(FLAG_CORE_VISIBLE_TOOLS, default=False)
        next_draft = draft
        explicit_allowed = set(input_data.allowed_tools_policy.allowed_names)
        explicit_policy = not input_data.allowed_tools_policy.allow_all
        for tool_name, record in draft.tools.items():
            outside_core = core_visible_only and tool_name not in _CORE_VISIBLE_TOOLS
            if not record.spec.execution_traits.requires_discovery and not outside_core:
                continue
            if tool_name in input_data.discovered_tools:
                continue
            if explicit_policy and tool_name in explicit_allowed:
                continue
            updated = record.model_copy(
                update={"visible_to_llm": False, "callable": False}
            )
            next_draft = next_draft.with_tool(tool_name, updated).append_trace(
                DecisionTraceEntry(
                    tool_name=tool_name,
                    reducer=self.name,
                    action="hide",
                    reason=(
                        "tool_outside_core_visible_set"
                        if outside_core
                        else "tool_requires_discovery"
                    ),
                    details={
                        "discovered": False,
                        "explicitly_allowed": explicit_policy and tool_name in explicit_allowed,
                        "core_visible_only": core_visible_only,
                    },
                )
            )
        return next_draft


class DiscoveryReducer:
    name = "DiscoveryReducer"

    def apply(
        self,
        input_data: GovernanceInput,
        draft: ResolvedToolSetDraft,
    ) -> ResolvedToolSetDraft:
        if input_data.runtime_mode.discovery_mode:
            return draft
        next_draft = draft
        for tool_name, record in draft.tools.items():
            if tool_name in input_data.discovered_tools:
                continue
            updated = record.model_copy(update={"searchable": False})
            next_draft = next_draft.with_tool(tool_name, updated).append_trace(
                DecisionTraceEntry(
                    tool_name=tool_name,
                    reducer=self.name,
                    action="search_exclude",
                    reason="discovery_disabled_for_runtime_mode",
                )
            )
        return next_draft


class SkillGatedReducer:
    """隐藏 skill_gated 工具，除非当前激活的 Skill 的 allowed_tools 包含该工具。

    机制用途：领域专用工具声明 skill_gated 后，仅在对应 Skill 激活时
    可见，避免在通用对话中暴露。
    """

    name = "SkillGatedReducer"

    def apply(
        self,
        input_data: GovernanceInput,
        draft: ResolvedToolSetDraft,
    ) -> ResolvedToolSetDraft:
        next_draft = draft
        for tool_name, record in draft.tools.items():
            if not record.spec.permission_traits.skill_gated:
                continue
            if tool_name in input_data.skill_allowed_tools:
                continue
            updated = record.model_copy(
                update={"visible_to_llm": False, "callable": False, "searchable": False}
            )
            next_draft = next_draft.with_tool(tool_name, updated).append_trace(
                DecisionTraceEntry(
                    tool_name=tool_name,
                    reducer=self.name,
                    action="hide",
                    reason="skill_gated_tool_not_unlocked",
                    details={
                        "active_skill": input_data.active_skill_name,
                        "skill_allowed_tools": sorted(input_data.skill_allowed_tools),
                    },
                )
            )
        return next_draft


class ToolGovernance:
    def __init__(
        self,
        *,
        reducers: Sequence[ToolGovernanceReducer] | None = None,
    ) -> None:
        self._reducers = tuple(
            reducers
            or (
                AllowedToolsPolicyReducer(),
                ActorIdentityReducer(),
                ReadOnlyPermissionReducer(),
                HeadlessModeReducer(),
                DeferredToolReducer(),
                SkillGatedReducer(),
                DiscoveryReducer(),
            )
        )

    def add_reducers(self, reducers: Sequence[ToolGovernanceReducer]) -> None:
        """追加 Reducer（不替代默认 7 个）"""
        self._reducers = tuple(list(self._reducers) + list(reducers))

    def resolve(self, input_data: GovernanceInput) -> ResolvedToolSet:
        seen_names: set[str] = set()
        duplicate_names: set[str] = set()
        for spec in input_data.catalog:
            if spec.name in seen_names:
                duplicate_names.add(spec.name)
            else:
                seen_names.add(spec.name)
        if duplicate_names:
            duplicates = ", ".join(sorted(duplicate_names))
            raise ValueError(f"duplicate tool names in governance catalog: {duplicates}")
        draft = ResolvedToolSetDraft(
            tools={
                spec.name: ResolvedToolRecord(spec=spec)
                for spec in input_data.catalog
            }
        )
        for reducer in self._reducers:
            draft = reducer.apply(input_data, draft)
        visible = tuple(
            name for name, record in draft.tools.items() if record.visible_to_llm
        )
        callable_tools = tuple(
            name for name, record in draft.tools.items() if record.callable
        )
        searchable = tuple(
            name for name, record in draft.tools.items() if record.searchable
        )
        denied = tuple(
            name for name, record in draft.tools.items() if not record.callable
        )
        return ResolvedToolSet(
            all_resolved_tools=draft.tools,
            visible_tools_for_llm=visible,
            callable_tools=callable_tools,
            denied_tools=denied,
            searchable_tools=searchable,
            decision_trace=draft.decision_trace,
        )


def resolve_registry_tool_governance(
    registry: ToolRegistry,
    *,
    skill_invocation: SkillInvocationState | None,
    allowed_tools_policy: AllowedToolsPolicy | None = None,
    discovered_tools: set[str] | tuple[str, ...] | list[str] | None = None,
    mode: WorkspaceMode = "work",
    subagent_depth: int = 0,
    headless: bool = False,
    governance: ToolGovernance | None = None,
) -> ResolvedToolSet:
    normalized_discovered = normalize_registry_tool_names(
        registry,
        discovered_tools or (),
    )
    return (governance or ToolGovernance()).resolve(
        GovernanceInput(
            catalog=registry_tool_specs(registry),
            actor_identity="subagent" if subagent_depth > 0 else "main",
            runtime_mode=RuntimeMode(headless_mode=headless),
            allowed_tools_policy=allowed_tools_policy
            or allowed_tools_policy_for_skill(
                registry,
                skill_invocation,
            ),
            active_skill_name=(
                skill_invocation.skill_name if skill_invocation is not None else None
            ),
            mode=mode,
            discovered_tools=frozenset(normalized_discovered),
            skill_allowed_tools=frozenset(
                skill_invocation.allowed_tools_override or ()
            ) if skill_invocation is not None else frozenset(),
        )
    )
def registry_tool_specs(registry: ToolRegistry) -> list[ToolSpec]:
    specs: list[ToolSpec] = []
    for name in registry.names():
        tool = registry.get(name)
        if tool is None:
            continue
        specs.append(tool_to_spec(tool))
    return specs


def tool_to_spec(tool: Tool) -> ToolSpec:
    tool_class = getattr(tool, "tool_class", "write")
    visibility_scope: VisibilityScope = "all"
    raw_visibility_scope = getattr(tool, "visibility_scope", None)
    if raw_visibility_scope in {"all", "main_only", "subagent_only"}:
        visibility_scope = raw_visibility_scope
    elif bool(getattr(tool, "parent_only", False)):
        visibility_scope = "main_only"
    executor_kind = (
        "mcp_remote" if hasattr(tool, "server_config") or ":" in tool.name else "local"
    )
    return ToolSpec(
        name=tool.name,
        description=getattr(tool, "description", "") or "",
        input_schema=getattr(tool, "parameters", {}) or {},
        execution_traits=ToolExecutionTraits(
            concurrency_safe=bool(getattr(tool, "is_concurrency_safe", False)),
            timeout_seconds=getattr(tool, "timeout_seconds", None),
            requires_discovery=bool(getattr(tool, "deferred", False)),
        ),
        permission_traits=ToolPermissionTraits(
            tool_class=tool_class,
            visibility_scope=visibility_scope,
            approval_eligible=bool(getattr(tool, "approval_eligible", True)),
            read_only_allowed=bool(
                getattr(tool, "read_only_allowed", tool_class != "write")
            ),
            parent_only=bool(getattr(tool, "parent_only", False)),
            requires_approval=bool(getattr(tool, "requires_approval", False)),
            requires_interactive_user=bool(
                getattr(tool, "requires_interactive_user", False)
            ),
            forced_chat_only=getattr(cfg, "forced_chat_only", False) if (cfg := getattr(tool, "server_config", None)) is not None else False,
            skill_gated=bool(getattr(tool, "skill_gated", False)),
        ),
        executor_kind=executor_kind,
    )


def allowed_tools_policy_for_skill(
    registry: ToolRegistry,
    skill_invocation: SkillInvocationState | None,
) -> AllowedToolsPolicy:
    if (
        skill_invocation is None
        or skill_invocation.allowed_tools_override is None
    ):
        return AllowedToolsPolicy()
    allowed_names = tuple(
        sorted(
            normalize_registry_tool_names(
                registry,
                skill_invocation.allowed_tools_override,
            )
        )
    )
    return AllowedToolsPolicy(
        allow_all=False,
        allowed_names=allowed_names,
    )


def normalize_registry_tool_names(
    registry: ToolRegistry,
    raw_names: set[str] | tuple[str, ...] | list[str],
) -> set[str]:
    registered = registry.names()
    lower_map: dict[str, str] = {n.lower(): n for n in registered}

    normalized: set[str] = set()
    dropped: list[str] = []
    for raw_name in raw_names:
        tool = registry.get(raw_name)
        if tool is not None:
            normalized.add(tool.name)
            continue
        # 大小写不敏感回退
        canonical = lower_map.get(raw_name.lower())
        if canonical is not None:
            normalized.add(canonical)
            logger.info(
                "normalize_registry_tool_names: case-insensitive match %r → %r",
                raw_name, canonical,
            )
            continue
        dropped.append(raw_name)

    if dropped:
        logger.warning(
            "normalize_registry_tool_names: dropped unregistered names: %s",
            ", ".join(dropped),
        )
    return normalized
