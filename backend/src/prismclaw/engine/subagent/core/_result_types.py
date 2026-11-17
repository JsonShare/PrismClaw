"""Result types and pure helper functions for AgentInstance.

Extracted from ``instance.py`` to keep the AgentInstance class focused
on orchestration logic while standalone data classes and pure functions
live in a lighter module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from prismclaw.schema.content.content_types import ToolResult
from prismclaw.schema.services.skills import SkillInvocationState


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _coerce_skill_invocation(invoked_skill_state: Any) -> SkillInvocationState | None:
    raw_state = invoked_skill_state
    if not isinstance(raw_state, dict):
        return None
    raw_active_runtime = raw_state.get("active_runtime")
    if isinstance(raw_active_runtime, SkillInvocationState):
        return raw_active_runtime
    if not isinstance(raw_active_runtime, dict):
        return None
    try:
        return SkillInvocationState.model_validate(raw_active_runtime)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class AgentRunResult:
    """Aggregate returned by ``AgentInstance.run()``."""

    tool_result: ToolResult
    outcome: str
    turns: int
    tokens_input: int
    tokens_output: int
    duration_seconds: float
    model_used: str


@dataclass(frozen=True)
class SettledSubagentToolSurface:
    visible_tool_names: tuple[str, ...]
    callable_tool_names: tuple[str, ...]
