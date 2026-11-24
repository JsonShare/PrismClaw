"""Prepared call types — prepare/execute two-phase separation (aligned with Pi P2).

Pi separates tool calls into `prepare` and `execute` phases, with `beforeToolCall`
interception possible between them. This module provides the type definitions
for that two-phase pattern.

Usage:
    prepared = await prepare_call(tool_call, context, registry)
    if isinstance(prepared, ImmediateResult):
        return prepared.result
    result = await execute_prepared(prepared, signal=abort_event)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from prismclaw.engine.toolkit.registry.tool_runtime import Tool
    from prismclaw.schema.content.content_types import ToolResult


@dataclass
class PreparedCall:
    """A prepared but not yet executed tool call — aligned with Pi's preparedToolCall.

    After the prepare phase, the tool call has been validated and is
    ready for execution. The execute phase cannot be intercepted.
    """

    tool_call: dict[str, Any]
    tool: Tool
    args: dict[str, Any]


@dataclass
class ImmediateResult:
    """An immediate result that doesn't require execution — aligned with Pi's immediateToolCall.

    This covers cases where the tool call was intercepted (e.g. tool not found)
    and we can return a result immediately without entering the execute phase.
    """

    tool_call: dict[str, Any]
    result: ToolResult
    is_error: bool = False
