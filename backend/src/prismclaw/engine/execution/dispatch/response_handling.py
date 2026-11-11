from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from prismclaw.engine.execution.dispatch.turn_utils import classify_turn, strip_thinking
from prismclaw.engine.task_profile.service import TaskResponsePolicy
from prismclaw.schema.agent.context import ContextTurnFinalizer
from prismclaw.engine.hooks import ResponseObservation
from prismclaw.schema.session.types import now_iso, generate_entry_id
from prismclaw.schema.agent.session import MessageEntry
from prismclaw.schema.content.streaming import Done

NoToolAction = Literal["retry", "complete"]


@dataclass
class NoToolTurnDecision:
    action: NoToolAction
    classification: str


class _ResponseHandlingDeps(Protocol):
    context_turn_finalizer: ContextTurnFinalizer
    hooks: Any


def retry_limit_for(
    classification: str,
    config: Any,
    response_policy: TaskResponsePolicy | None = None,
) -> int:
    if response_policy is not None:
        if classification == "reasoning":
            return response_policy.reasoning_retry_limit
        if classification == "empty":
            return response_policy.empty_retry_limit
        return 0
    if classification == "reasoning":
        return config.retry.reasoning_only_limit
    if classification == "empty":
        return config.retry.empty_response_limit
    return 0


def decide_no_tool_turn(
    *,
    response: Any,
    config: Any,
    retry_counts: dict[str, int],
    response_policy: TaskResponsePolicy | None = None,
) -> NoToolTurnDecision:
    classification = classify_turn(
        text=response.text,
        tool_calls=response.tool_calls,
    )
    retry_limit = retry_limit_for(classification, config, response_policy)
    should_retry = (
        classification in {"reasoning", "empty"}
        and retry_limit > 0
        and retry_counts[classification] < retry_limit
    )
    return NoToolTurnDecision(
        action="retry" if should_retry else "complete",
        classification=classification,
    )


async def append_assistant_response(
    *,
    deps: _ResponseHandlingDeps,
    tree: Any,
    append_entry: Any,
    text: str,
    tool_calls: list[dict[str, Any]] | None = None,
) -> MessageEntry:
    # 模型可能在 <thinking>...</thinking> 里写内部推理草稿——
    # 哪怕有 tool_calls（工具回合）也不能把 thinking block 落树。
    # strip_thinking 对无 block 的纯文本是 no-op，安全。
    cleaned = strip_thinking(text or "")
    entry = MessageEntry(
        id=generate_entry_id(set(tree.entries.keys())),
        parent_id=tree.leaf_id,
        timestamp=now_iso(),
        role="assistant",
        content=cleaned,
        tool_calls=tool_calls or [],
    )
    await append_entry(deps, tree, entry)
    return entry


async def complete_no_tool_turn(
    *,
    deps: _ResponseHandlingDeps,
    request: Any,
    tree: Any,
    session_messages: list[dict[str, Any]],
    append_entry: Any,
    response_text: str,
    usage_totals: dict[str, int],
) -> Done:
    # 入口处统一 strip_thinking：assistant 落树、hooks 观测、以及
    # 回灌给 next turn 的 session_messages 都不该看到模型内部草稿。
    cleaned = strip_thinking(response_text or "")
    assistant_entry = await append_assistant_response(
        deps=deps,
        tree=tree,
        append_entry=append_entry,
        text=cleaned,
    )
    await deps.hooks.notify_response(
        ResponseObservation(
            session_id=request.session_id,
            assistant_text=cleaned,
            tool_calls=[],
        )
    )
    finalizer_messages = list(session_messages)
    finalizer_messages.append(
        {
            "role": "assistant",
            "content": cleaned,
            "__entry_id__": assistant_entry.id,
        }
    )
    await deps.context_turn_finalizer.after_turn(
        request.session_id,
        finalizer_messages,
    )
    return Done(
        final_message=response_text,
        assistant_message_id=assistant_entry.id,
        usage=usage_totals,
    )
