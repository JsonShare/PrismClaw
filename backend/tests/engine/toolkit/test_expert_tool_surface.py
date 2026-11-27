from __future__ import annotations

from typing import Literal

import pytest

from prismclaw.engine.execution.tools.expert_mode import (
    DEFERRED_TEAM_TOOLS,
    EXPERT_MODE_TOOLS,
    TEAM_LEAD_TOOLS,
)
from prismclaw.engine.experts.codes import ExpertError, ExpertErrorCode
from prismclaw.engine.experts.scope import ToolSurface
from prismclaw.engine.toolkit.registry.governance import (
    GovernanceInput,
    ResolvedToolSet,
    ToolGovernance,
)
from prismclaw.engine.toolkit.registry.specs import (
    ToolExecutionTraits,
    ToolPermissionTraits,
    ToolSpec,
)

_BASE_TOOL_NAMES = frozenset(
    {
        "read",
        "write",
        "edit",
        "glob",
        "grep",
        "bash",
        "todo_write",
        "present_files",
        "ask_user_question",
        "activate_skill",
        "skill_search",
        "tool_search",
    }
)


def _spec(name: str, *, deferred: bool = False) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=f"{name} description",
        input_schema={"type": "object", "properties": {}},
        execution_traits=ToolExecutionTraits(requires_discovery=deferred),
        permission_traits=ToolPermissionTraits(),
        executor_kind="mcp_remote" if ":" in name else "local",
    )


def _surface(*, team: bool = False, restricted: bool = True) -> ToolSurface:
    active = EXPERT_MODE_TOOLS | TEAM_LEAD_TOOLS if team else EXPERT_MODE_TOOLS
    return ToolSurface(
        active=active,
        deferred=active | DEFERRED_TEAM_TOOLS if team else active,
        member=EXPERT_MODE_TOOLS,
        restricted=restricted,
    )


def _resolve(
    catalog: list[ToolSpec],
    *,
    surface: ToolSurface,
    actor_identity: Literal["main", "subagent"] = "main",
    discovered_tools: frozenset[str] = frozenset(),
) -> ResolvedToolSet:
    return ToolGovernance().resolve(
        GovernanceInput(
            catalog=catalog,
            actor_identity=actor_identity,
            discovered_tools=discovered_tools,
            tool_surface=surface,
        )
    )


def test_four_tool_surface_sets_match_the_spec_exactly() -> None:
    assert EXPERT_MODE_TOOLS == _BASE_TOOL_NAMES
    assert len(EXPERT_MODE_TOOLS) == 12
    assert frozenset({"sub_agent", "subagent_control"}) == TEAM_LEAD_TOOLS
    assert frozenset({"team_create", "team_delete"}) == DEFERRED_TEAM_TOOLS


def test_expert_session_has_exactly_twelve_builtin_tools() -> None:
    catalog = [_spec(name) for name in sorted(EXPERT_MODE_TOOLS | {"memory"})]

    resolved = _resolve(catalog, surface=_surface())

    assert set(resolved.visible_tools_for_llm) == EXPERT_MODE_TOOLS
    assert set(resolved.callable_tools) == EXPERT_MODE_TOOLS
    assert set(resolved.searchable_tools) == EXPERT_MODE_TOOLS
    memory = resolved.all_resolved_tools["memory"]
    assert not memory.visible_to_llm
    assert not memory.callable
    assert not memory.searchable
    assert any(
        trace.reducer == "ExpertToolSurfaceReducer"
        and trace.tool_name == "memory"
        and trace.reason == "tool_not_in_expert_surface"
        for trace in resolved.decision_trace
    )


def test_mcp_tool_is_callable_searchable_and_traced_as_skipped() -> None:
    mcp_name = "legal-core.search:lookup"
    resolved = _resolve(
        [_spec("read"), _spec("tool_search"), _spec(mcp_name)],
        surface=_surface(),
    )

    mcp = resolved.all_resolved_tools[mcp_name]
    assert mcp.visible_to_llm
    assert mcp.callable
    assert mcp.searchable
    assert "tool_search" in resolved.callable_tools
    assert mcp_name in resolved.searchable_tools
    assert any(
        trace.reducer == "ExpertToolSurfaceReducer"
        and trace.tool_name == mcp_name
        and trace.reason == "mcp_tool_skipped"
        for trace in resolved.decision_trace
    )


def test_team_lead_deferred_unlock_and_member_surfaces() -> None:
    team_active = EXPERT_MODE_TOOLS | TEAM_LEAD_TOOLS
    team_deferred = team_active | DEFERRED_TEAM_TOOLS
    catalog = [_spec(name) for name in sorted(team_deferred | {"memory"})]

    lead = _resolve(catalog, surface=_surface(team=True))
    assert set(lead.visible_tools_for_llm) == team_active
    assert set(lead.callable_tools) == team_active
    assert len(lead.callable_tools) == 14
    assert set(lead.searchable_tools) == team_deferred
    assert len(lead.searchable_tools) == 16

    unlocked = _resolve(
        catalog,
        surface=_surface(team=True),
        discovered_tools=DEFERRED_TEAM_TOOLS,
    )
    assert set(unlocked.visible_tools_for_llm) == team_deferred
    assert set(unlocked.callable_tools) == team_deferred
    assert len(unlocked.callable_tools) == 16

    member = _resolve(catalog, surface=_surface(team=True), actor_identity="subagent")
    assert set(member.visible_tools_for_llm) == EXPERT_MODE_TOOLS
    assert set(member.callable_tools) == EXPERT_MODE_TOOLS
    assert set(member.searchable_tools) == EXPERT_MODE_TOOLS
    assert len(member.callable_tools) == 12


def test_empty_whitelist_intersection_is_rejected_fail_closed() -> None:
    catalog = [_spec("memory"), _spec("goal")]

    with pytest.raises(ExpertError) as exc_info:
        _resolve(catalog, surface=_surface())

    assert exc_info.value.code == ExpertErrorCode.EXPERT_TOOL_SURFACE_EMPTY
    assert exc_info.value.detail == {
        "actor_identity": "main",
        "registered_builtin": ["goal", "memory"],
    }
