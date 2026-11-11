"""Prepared call types — prepare/execute two-phase separation (aligned with Pi P2).

Pi separates tool calls into `prepare` and `execute` phases, with `beforeToolCall`
interception possible between them. This module provides the type definitions
for that two-phase pattern.

Usage:
    prepared = await prepare_call(tool_call, context, safety_gate)
    if isinstance(prepared, ImmediateResult):
        return prepared.result
    result = await execute_prepared(prepared, signal=abort_event)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from prismclaw.schema.content.content_types import ToolCall, ToolResult


@dataclass
class PreparedCall:
    """A prepared but not yet executed tool call — aligned with Pi's preparedToolCall.

    After the prepare phase, the tool call has been validated, safety-checked,
    and is ready for execution. The execute phase cannot be intercepted.
    """
    tool_call: ToolCall
    tool: Any  # Tool Protocol instance — avoiding execution import
    args: dict[str, Any]
    safety_decision: Any | None = None  # SafetyDecision — avoiding execution import


@dataclass
class ImmediateResult:
    """An immediate result that doesn't require execution — aligned with Pi's immediateToolCall.

    This covers cases where the tool call was intercepted (not found, blocked by safety)
    and we can return a result immediately without entering the execute phase.
    """
    tool_call: ToolCall
    result: ToolResult
    is_error: bool = False
