from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from prismclaw.engine.execution.checkpoint.models import CommitState, ToolEffectRecord
from prismclaw.engine.subagent.core.persistence import SubagentPersistence
from prismclaw.schema.agent.capability import EffectClass
from prismclaw.schema.session.types import (
    SUBAGENT_TOOL_EFFECT_CUSTOM_TYPE,
    CustomEntry,
    MessageEntry,
    generate_entry_id,
)

_BEFORE_DISPATCH = "interrupted_before_dispatch"
_PURE_READ_INTERRUPTED = "interrupted_pure_read"
_CONFIRMED_UNCERTAINTY = "confirmed_uncertainty"
_SAFE_REPAIR_ACTIONS = {_BEFORE_DISPATCH, _PURE_READ_INTERRUPTED}


@dataclass(slots=True)
class RecoveryAssessment:
    blocked: bool
    reasons: list[str] = field(default_factory=list)
    repaired_tool_call_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _AssistantToolCall:
    tool_call_id: str
    tool_name: str
    agent_id: str
    run_id: str
    attempt: int
    arguments_summary: str


async def assess_and_repair_recovery(
    persistence: SubagentPersistence,
    *,
    confirm_recovery: bool = False,
) -> RecoveryAssessment:
    """Close interrupted tool calls without dispatching or replaying any tool."""
    await persistence.refresh()
    branch = persistence.tree.get_branch()
    calls: dict[str, _AssistantToolCall] = {}
    tool_results: dict[str, list[MessageEntry]] = {}
    latest_effects: dict[str, dict[str, Any]] = {}

    for entry in branch:
        if isinstance(entry, MessageEntry) and entry.role == "assistant":
            metadata = entry.metadata or {}
            # 继承自父会话的消息只提供上下文，其 tool_calls 不属于本子代理，
            # 不能当成未完成调用去补写中断结果。
            if metadata.get("kind") == "inherited":
                continue
            for raw_call in entry.tool_calls or []:
                call_id = str(raw_call.get("id") or "")
                if not call_id:
                    continue
                calls[call_id] = _assistant_tool_call(persistence, raw_call, metadata)
        elif isinstance(entry, MessageEntry) and entry.role == "tool":
            metadata = entry.metadata or {}
            call_id = entry.tool_call_id or metadata.get("tool_call_id")
            if isinstance(call_id, str) and call_id:
                tool_results.setdefault(call_id, []).append(entry)
        elif (
            isinstance(entry, CustomEntry)
            and entry.custom_type == SUBAGENT_TOOL_EFFECT_CUSTOM_TYPE
            and isinstance(entry.data, dict)
        ):
            call_id = entry.data.get("tool_call_id")
            if isinstance(call_id, str) and call_id:
                latest_effects[call_id] = dict(entry.data)

    blocked = False
    reasons: list[str] = []
    repaired: list[str] = []
    repair_entries: list[MessageEntry | CustomEntry] = []
    existing_ids = persistence.tree.all_entry_ids()

    for call_id, call in calls.items():
        results = tool_results.get(call_id, [])
        has_result = bool(results)
        synthetic_results = [result for result in results if _is_synthetic_result(result)]
        effect = latest_effects.get(call_id)
        state = _enum_value(effect.get("commit_state")) if effect is not None else ""
        effect_class = _enum_value(effect.get("effect_class")) if effect is not None else ""
        recovery_action = str(effect.get("recovery_action") or "") if effect is not None else ""

        if (
            effect is not None
            and state == CommitState.ROLLED_BACK.value
            and effect.get("result_persisted") is True
            and recovery_action in _SAFE_REPAIR_ACTIONS
            and any(_recovery_action(result) == recovery_action for result in synthetic_results)
        ):
            continue

        if effect is not None and effect.get("recovery_confirmed") is True and has_result:
            reasons.append(
                str(
                    effect.get("recovery_reason")
                    or _risk_reason(call_id, state, effect_class, has_result=False)
                )
            )
            continue

        if effect is None and not has_result:
            _stage_repair(
                persistence,
                repair_entries,
                existing_ids,
                call,
                effect=None,
                commit_state=CommitState.ROLLED_BACK,
                recovery_action=_BEFORE_DISPATCH,
                write_tool_result=True,
            )
            repaired.append(call_id)
            continue

        if effect is None and synthetic_results:
            synthetic_action = _recovery_action(synthetic_results[-1])
            if synthetic_action in _SAFE_REPAIR_ACTIONS:
                _stage_repair(
                    persistence,
                    repair_entries,
                    existing_ids,
                    call,
                    effect=None,
                    commit_state=CommitState.ROLLED_BACK,
                    recovery_action=synthetic_action,
                    write_tool_result=False,
                )
                repaired.append(call_id)
                continue

        if state == CommitState.COMMITTED.value and has_result:
            continue

        if (
            state == CommitState.IN_PROGRESS.value
            and effect_class == EffectClass.PURE_READ.value
            and (not has_result or synthetic_results)
        ):
            _stage_repair(
                persistence,
                repair_entries,
                existing_ids,
                call,
                effect=effect,
                commit_state=CommitState.ROLLED_BACK,
                recovery_action=_PURE_READ_INTERRUPTED,
                write_tool_result=not has_result,
            )
            repaired.append(call_id)
            continue

        reason = _risk_reason(call_id, state, effect_class, has_result=has_result)
        reasons.append(reason)
        already_confirmed = effect is not None and effect.get("recovery_confirmed") is True
        if confirm_recovery or already_confirmed:
            _stage_repair(
                persistence,
                repair_entries,
                existing_ids,
                call,
                effect=effect,
                commit_state=state or CommitState.COMMIT_UNKNOWN.value,
                recovery_action=_CONFIRMED_UNCERTAINTY,
                write_tool_result=not has_result,
                recovery_reason=reason,
            )
            repaired.append(call_id)
        else:
            blocked = True

    if repair_entries:
        await persistence.commit_entries(repair_entries)
    return RecoveryAssessment(
        blocked=blocked,
        reasons=reasons,
        repaired_tool_call_ids=repaired,
    )


def _assistant_tool_call(
    persistence: SubagentPersistence,
    raw_call: dict[str, Any],
    assistant_metadata: dict[str, Any],
) -> _AssistantToolCall:
    function = raw_call.get("function")
    if not isinstance(function, dict):
        function = {}
    tool_name = str(function.get("name") or "unknown")
    attempt = assistant_metadata.get("attempt")
    if not isinstance(attempt, int):
        attempt = 1
    return _AssistantToolCall(
        tool_call_id=str(raw_call.get("id") or ""),
        tool_name=tool_name,
        agent_id=str(assistant_metadata.get("agent_id") or persistence.tree.header.agent_id),
        run_id=str(assistant_metadata.get("run_id") or ""),
        attempt=attempt,
        arguments_summary=_arguments_summary(function.get("arguments")),
    )


def _stage_repair(
    persistence: SubagentPersistence,
    entries: list[MessageEntry | CustomEntry],
    existing_ids: set[str],
    call: _AssistantToolCall,
    *,
    effect: dict[str, Any] | None,
    commit_state: str | CommitState,
    recovery_action: str,
    write_tool_result: bool,
    recovery_reason: str | None = None,
) -> None:
    if write_tool_result:
        metadata: dict[str, Any] = {
            "agent_id": call.agent_id,
            "run_id": call.run_id,
            "attempt": call.attempt,
            "tool_call_id": call.tool_call_id,
            "synthetic": True,
            "subagent_recovery": True,
            "recovery_action": recovery_action,
            "original_call_replayed": False,
        }
        if recovery_action == _BEFORE_DISPATCH:
            metadata["interrupted_before_dispatch"] = True
            metadata["original_call_executed"] = False
        if recovery_action == _CONFIRMED_UNCERTAINTY:
            metadata["uncertainty_accepted"] = True
        result = MessageEntry(
            id=_new_entry_id(existing_ids),
            parent_id=persistence.tree.leaf_id,
            role="tool",
            content=_synthetic_result_content(recovery_action),
            tool_call_id=call.tool_call_id,
            tool_name=call.tool_name,
            is_error=True,
            metadata=metadata,
        )
        entries.append(result)

    if effect is None:
        payload = ToolEffectRecord(
            tool_call_id=call.tool_call_id,
            tool_name=call.tool_name,
            effect_class=EffectClass.UNKNOWN_EFFECT,
            idempotency_key=call.tool_call_id,
        ).model_dump(mode="json")
    else:
        payload = dict(effect)
    payload.update(
        {
            "tool_call_id": call.tool_call_id,
            "tool_name": payload.get("tool_name") or call.tool_name,
            "commit_state": _enum_value(commit_state),
            "result_persisted": True,
            "finished_at_epoch_ms": _now_epoch_ms(),
            "effect_id": payload.get("effect_id") or f"fx:{uuid.uuid4()}",
            "agent_id": payload.get("agent_id") or call.agent_id,
            "run_id": payload.get("run_id") or call.run_id,
            "attempt": payload.get("attempt") or call.attempt,
            "fence": payload.get("fence", 0),
            "arguments_summary": payload.get("arguments_summary") or call.arguments_summary,
            "tool_is_error": True,
            "recovery_action": recovery_action,
            "original_call_replayed": False,
        }
    )
    if recovery_action == _BEFORE_DISPATCH:
        payload["original_call_executed"] = False
    if recovery_action == _CONFIRMED_UNCERTAINTY:
        payload["recovery_confirmed"] = True
        payload["uncertainty_accepted"] = True
        payload["recovery_reason"] = recovery_reason
    entries.append(
        CustomEntry(
            id=_new_entry_id(existing_ids),
            parent_id=persistence.tree.leaf_id,
            custom_type=SUBAGENT_TOOL_EFFECT_CUSTOM_TYPE,
            data=payload,
        )
    )


def _synthetic_result_content(recovery_action: str) -> str:
    if recovery_action == _BEFORE_DISPATCH:
        return (
            "Recovery: interrupted before dispatch; the original call was not executed "
            "and will not be replayed."
        )
    if recovery_action == _PURE_READ_INTERRUPTED:
        return (
            "Recovery: the read-only call was interrupted; the original call was not "
            "replayed and no original result is available."
        )
    return (
        "Recovery confirmed: uncertainty accepted; the original call was not replayed. "
        "Any side effects of the interrupted call remain uncertain."
    )


def _risk_reason(
    call_id: str,
    state: str,
    effect_class: str,
    *,
    has_result: bool,
) -> str:
    if state == CommitState.COMMIT_UNKNOWN.value:
        return f"tool call {call_id!r} has an unknown commit outcome"
    if state == CommitState.COMMITTED.value and not has_result:
        return f"tool call {call_id!r} is committed but has no tool result"
    if state == CommitState.IN_PROGRESS.value:
        return (
            f"tool call {call_id!r} is still in progress with effect class "
            f"{effect_class or EffectClass.UNKNOWN_EFFECT.value!r}"
        )
    if not state:
        return f"tool call {call_id!r} has a tool result but no effect record"
    return (
        f"tool call {call_id!r} has inconsistent recovery state {state!r} "
        f"(tool result present: {has_result})"
    )


def _is_synthetic_result(entry: MessageEntry) -> bool:
    metadata = entry.metadata or {}
    return metadata.get("synthetic") is True and metadata.get("subagent_recovery") is True


def _recovery_action(entry: MessageEntry) -> str:
    return str((entry.metadata or {}).get("recovery_action") or "")


def _enum_value(value: Any) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value or "")


def _new_entry_id(existing_ids: set[str]) -> str:
    entry_id = generate_entry_id(existing_ids)
    existing_ids.add(entry_id)
    return entry_id


def _arguments_summary(raw_arguments: Any) -> str:
    if isinstance(raw_arguments, str):
        text = raw_arguments
    else:
        try:
            text = json.dumps(raw_arguments or {}, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(raw_arguments)
    return text[:1000]


def _now_epoch_ms() -> int:
    return int(time.time() * 1000)
