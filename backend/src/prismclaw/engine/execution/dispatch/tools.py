"""Tool execution logic: SafetyGate filtering, tool dispatch, result processing.

Extracted from ``runner.py`` to isolate the tool execution layer from the
main agent loop.  This module owns:

* SafetyGate evaluation and denial emission
* Pre-tool hook dispatch and denial collection
* Tool execution with handoff dispatch
* Result postprocessing (skill strategies, context modifiers)
* Evidence building from tool execution results
* Audit store integration (tool call start/complete/denied)

The caller (``run_agent_stream``) retains responsibility for:
* Parsing tool calls from LLM response
* Tool loop policy evaluation
* Post-tool observations (delivery ledger / same-target read counts)
"""
from __future__ import annotations

import logging
from uuid import uuid4
from typing import Any

from prismclaw.engine.execution.loop.arbiter import RunContext
from prismclaw.engine.execution.dispatch.call_handling import ParsedToolCall
from prismclaw.engine.hooks import _dispatch_pre_tool_hooks
from prismclaw.engine.security.dlp.sanitizer import sanitize_tool_args
from prismclaw.engine.security.audit.audit_types import ToolCallStatus


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SafetyGate evaluation
# ---------------------------------------------------------------------------

async def evaluate_safety_gate(
    ctx: RunContext,
    parsed_calls: list[ParsedToolCall],
    *,
    mode: str,
    workspace_tag: Any = None,
    traj_turn_counter: int = 0,
    iteration: int = 0,
) -> tuple[dict[str, str], dict[str, str], list[ParsedToolCall]]:
    """Run SafetyGate + pre-tool hooks over parsed tool calls.

    纯判定层：只回答"谁能执行、谁被拒及原因"。被拒调用的流事件
    （ToolCallEnd）由调用方（run_loop 骨架 3a 的 _create_blocked_tool_result）
    统一构造，与 doom-loop 冻结拦截的产出形态一致。

    Returns:
        gate_denied:   call_id → reason for SafetyGate denials
        hook_denied:   call_id → reason for pre-tool hook denials
        allowed_calls: parsed_calls that passed all gates
    """
    deps = ctx.deps
    request = ctx.request
    tool_ctx = ctx._tool_ctx  # set by caller before invoking
    _audit_store = getattr(deps, "audit_store", None)

    _gate_denied: dict[str, str] = {}
    _audit_ids: dict[str, str] = {}

    if getattr(deps, "safety_gate", None) is not None:
        for _pc in parsed_calls:
            if _audit_store:
                _audit_id = uuid4().hex
                _args_hash, _args_sanitized, _args_preview = sanitize_tool_args(_pc.arguments or {})
                _audit_store.submit_tool_call_start(
                    tool_call_id=_audit_id,
                    session_id=getattr(request, "session_id", ""),
                    turn_seq=traj_turn_counter,
                    iteration_seq=iteration,
                    tool_name=_pc.tool_name,
                    args_hash=_args_hash,
                    args_sanitized=_args_sanitized,
                    args_preview=_args_preview,
                    tenant_id=getattr(tool_ctx, "tenant_id", None),
                )
                _audit_ids[_pc.call_id] = _audit_id

            _gate_result = await deps.safety_gate.evaluate(
                tool_name=_pc.tool_name,
                arguments=_pc.arguments or {},
                mode=mode,
                workspace_tag=workspace_tag,
                session_id=getattr(request, "session_id", ""),
                user_id=getattr(request, "user_id", ""),
                tenant_id=getattr(tool_ctx, "tenant_id", ""),
            )

            if _audit_store and _pc.call_id in _audit_ids:
                _risk_json = (
                    _gate_result.risk_assessment.model_dump_json()
                    if _gate_result.risk_assessment
                    else "{}"
                )
                _ws_flags = (
                    f"ew={tool_ctx.workspace_tag.allow_external_write}/rc={tool_ctx.workspace_tag.require_consent}/sd={tool_ctx.workspace_tag.restrict_chat_sensitive_data}/4e={tool_ctx.workspace_tag.four_eyes_required}"
                    if hasattr(tool_ctx, "workspace_tag") and tool_ctx.workspace_tag
                    else "none"
                )
                _consent: list[str] = []
                _audit_store.submit_tool_call_safety(
                    tool_call_id=_audit_ids[_pc.call_id],
                    sandbox_tier=_gate_result.sandbox_tier.value,
                    confirmation_kind=_gate_result.confirmation.value,
                    risk_assessment_json=_risk_json,
                    workspace_tag_flags=_ws_flags,
                    consent_purposes=_consent,
                )

            if not _gate_result.allowed:
                if _audit_store and _pc.call_id in _audit_ids:
                    _audit_store.submit_tool_call_complete(
                        tool_call_id=_audit_ids[_pc.call_id],
                        status=ToolCallStatus.DENIED,
                    )
                _gate_denied[_pc.call_id] = _gate_result.reason or "safety_gate_deny"
            else:
                if hasattr(tool_ctx, "current_sandbox_tier"):
                    tool_ctx.current_sandbox_tier = _gate_result.sandbox_tier

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
    all_denied = {**_gate_denied, **pre_tool_denied}
    allowed_calls = [c for c in parsed_calls if c.call_id not in all_denied]

    return _gate_denied, pre_tool_denied, allowed_calls



