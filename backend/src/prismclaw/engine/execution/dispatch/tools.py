"""Tool execution pre-checks: pre-tool hook dispatch and allowed-call computation.

Extracted from ``runner.py`` to isolate the tool execution layer from the
main agent loop.  This module owns:

* Pre-tool hook dispatch and denial collection
* Allowed-call computation after denials

The caller (``run_agent_stream``) retains responsibility for:
* Parsing tool calls from LLM response
* Tool loop policy evaluation
* Post-tool observations (delivery ledger / same-target read counts)
"""
from __future__ import annotations

import logging
from typing import Any

from prismclaw.engine.execution.loop.arbiter import RunContext
from prismclaw.engine.execution.dispatch.call_handling import ParsedToolCall
from prismclaw.engine.hooks import _dispatch_pre_tool_hooks


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pre-tool checks
# ---------------------------------------------------------------------------

async def run_pre_tool_checks(
    ctx: RunContext,
    parsed_calls: list[ParsedToolCall],
    *,
    workspace_tag: Any = None,
    traj_turn_counter: int = 0,
    iteration: int = 0,
) -> tuple[dict[str, str], list[ParsedToolCall]]:
    """Run pre-tool hooks over parsed tool calls.

    纯判定层：只回答"谁被 hook 拒绝及原因"。被拒调用的流事件
    （ToolCallEnd）由调用方（run_loop 骨架 3a 的 _create_blocked_tool_result）
    统一构造，与 doom-loop 冻结拦截的产出形态一致。

    Returns:
        hook_denied:   call_id → reason for pre-tool hook denials
        allowed_calls: parsed_calls that passed all checks
    """
    deps = ctx.deps
    request = ctx.request

    # Pre-tool hooks
    pre_tool_denied: dict[str, str] = {}
    if getattr(deps, "hook_dispatcher", None) is not None:
        _active_skill_name: str | None = None
        _loop_state = getattr(ctx, "loop_state", None)
        _skill_inv = getattr(_loop_state, "active_skill_invocation", None) if _loop_state else None
        if _skill_inv is not None:
            _active_skill_name = getattr(_skill_inv, "skill_name", None)
        pre_tool_denied = await _dispatch_pre_tool_hooks(
            dispatcher=deps.hook_dispatcher,
            parsed_calls=parsed_calls,
            session_id=request.session_id,
            cwd=ctx._tool_workspace_path,
            active_skill_name=_active_skill_name,
        )

    # Compute allowed calls
    allowed_calls = [c for c in parsed_calls if c.call_id not in pre_tool_denied]

    return pre_tool_denied, allowed_calls
