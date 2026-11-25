from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol

from prismclaw.engine.execution.dispatch.turn_utils import classify_turn, strip_noise
from prismclaw.engine.hooks import HookRegistry, ResponseObservation
from prismclaw.engine.task_profile.service import TaskResponsePolicy
from prismclaw.schema.agent.context import ContextTurnFinalizer
from prismclaw.schema.agent.session import MessageEntry
from prismclaw.schema.content.streaming import Done
from prismclaw.schema.session.types import SessionTree, generate_entry_id, now_iso

if TYPE_CHECKING:
    from prismclaw.schema.agent.types import RunRequest
    from prismclaw.schema.config.config import AgentRunConfig
    from prismclaw.schema.llm.types import LLMResponse

NoToolAction = Literal["retry", "complete"]


@dataclass
class NoToolTurnDecision:
    action: NoToolAction
    classification: str


class _ResponseHandlingDeps(Protocol):
    @property
    def context_turn_finalizer(self) -> ContextTurnFinalizer: ...

    @property
    def hooks(self) -> HookRegistry: ...


def retry_limit_for(
    classification: str,
    config: AgentRunConfig,
    response_policy: TaskResponsePolicy | None = None,
) -> int:
    if response_policy is not None:
        if classification == "reasoning":
            return response_policy.reasoning_retry_limit
        if classification == "empty":
            return response_policy.empty_retry_limit
        if classification == "tool_markup":
            return response_policy.tool_markup_retry_limit
        return 0
    if classification == "reasoning":
        return config.retry.reasoning_only_limit
    if classification == "empty":
        return config.retry.empty_response_limit
    if classification == "tool_markup":
        # 无 task_profile 时按空答复处理：同样是「没有可见答复」。
        return config.retry.empty_response_limit
    return 0


def decide_no_tool_turn(
    *,
    response: LLMResponse,
    config: AgentRunConfig,
    retry_counts: dict[str, int],
    response_policy: TaskResponsePolicy | None = None,
) -> NoToolTurnDecision:
    classification = classify_turn(
        text=response.text,
        tool_calls=response.tool_calls,
    )
    retry_limit = retry_limit_for(classification, config, response_policy)
    should_retry = (
        classification in {"reasoning", "empty", "tool_markup"}
        and retry_limit > 0
        and retry_counts.get(classification, 0) < retry_limit
    )
    return NoToolTurnDecision(
        action="retry" if should_retry else "complete",
        classification=classification,
    )


async def append_assistant_response(
    *,
    deps: _ResponseHandlingDeps,
    tree: SessionTree,
    append_entry: Callable[..., Awaitable[None]],
    text: str,
    tool_calls: list[dict[str, Any]] | None = None,
) -> MessageEntry:
    # 模型可能在 <thinking>...</thinking> 里写内部推理草稿，也可能在端点缺
    # tool-call parser 时把原生工具调用标记（<|DSML|tool_calls>…）以纯文本
    # 写进 content——哪怕有 tool_calls（工具回合）都不能落树。
    # strip_noise 对干净文本是 no-op，安全。
    cleaned = strip_noise(text or "")
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
    request: RunRequest,
    tree: SessionTree,
    session_messages: list[dict[str, Any]],
    append_entry: Callable[..., Awaitable[None]],
    response_text: str,
    usage_totals: dict[str, int],
) -> Done:
    # 入口处统一 strip_noise：assistant 落树、hooks 观测、以及
    # 回灌给 next turn 的 session_messages 都不该看到模型内部草稿与工具标记。
    cleaned = strip_noise(response_text or "")

    # 收敛场景 fallback：模型被收敛提示约束后可能输出空/极短文本（"好的"、
    # "直接结束"）——此时不再落空 assistant entry，也不把空文本当最终答复。
    # 从 tree 上读最后一条已落树的非空 assistant entry（通常是上一轮带
    # tool_calls 同时输出的完整正文，如 Iter 1 的 write 回合）。
    _min_final_len = 20  # 20 字符以下视为"未给出有效答复"，触发 fallback
    if len(cleaned) < _min_final_len:
        _fallback_text = ""
        _fallback_entry_id: str | None = None
        for seq in reversed(tree.order):
            _e = tree.entries.get(seq)
            if (
                _e is not None
                and getattr(_e, "role", None) == "assistant"
                and getattr(_e, "content", "")
                and len(str(getattr(_e, "content", ""))) >= _min_final_len
            ):
                _fallback_text = str(_e.content)
                _fallback_entry_id = _e.id
                break
        if _fallback_text and _fallback_entry_id:
            await deps.hooks.notify_response(
                ResponseObservation(
                    session_id=request.session_id,
                    assistant_text=cleaned,  # hooks 观测用实际模型输出
                    tool_calls=[],
                )
            )
            return Done(
                final_message=_fallback_text,
                assistant_message_id=_fallback_entry_id,
                usage=usage_totals,
            )
        # 无可用 fallback（极端场景：模型全程未输出任何有效答复）
        cleaned = cleaned or ""  # 保持原 cleaned，继续走完流程

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
        # 与实时推送同源：final_message 不能带 <thinking> 或工具调用标记，
        # 否则「审计/非 Web 通道的正文」与「用户实时看到的正文」分叉。
        final_message=cleaned,
        assistant_message_id=assistant_entry.id,
        usage=usage_totals,
    )
