from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Literal

from prismclaw.engine.foundation.tool_output import (  # noqa: F401
    _head_tail_budget,
    _text_length,
    resolve_max_output_chars,
    truncate_tool_result,
)
from prismclaw.schema.agent.capability import EffectClass
from prismclaw.schema.content.content_types import ToolResult
from prismclaw.schema.shared.tool_markup import has_tool_markup, strip_tool_markup

if TYPE_CHECKING:
    from prismclaw.engine.toolkit.registry.registry import ToolRegistry

# ---------------------------------------------------------------------------
# incomplete_turn
# ---------------------------------------------------------------------------

TurnClassification = Literal["ok", "reasoning", "empty", "tool_markup"]

_THINKING_BLOCK = re.compile(r"<thinking>.*?</thinking>", re.IGNORECASE | re.DOTALL)


def strip_thinking(text: str) -> str:
    return _THINKING_BLOCK.sub("", text or "").strip()


def strip_noise(text: str) -> str:
    """剥离一切不该出现在用户可见正文里的模型内部标记。

    = ``<thinking>`` 内部草稿 + 原生工具调用标记（端点缺 tool-call parser 时
    模型会把 ``<|DSML|tool_calls>…`` 以纯文本写进 content）。所有落树/推送/
    历史回放的出口都应走这里，而不是只用 ``strip_thinking``。
    """
    return strip_tool_markup(strip_thinking(text or "")).strip()


def extract_thinking(text: str) -> str:
    matches = _THINKING_BLOCK.findall(text or "")
    return "\n".join(matches)


def classify_turn(
    *,
    text: str,
    tool_calls: list[dict[str, Any]] | None,
    reasoning: str | None = None,
) -> TurnClassification:
    if tool_calls:
        return "ok"

    raw_text = text or ""
    has_reasoning = bool(reasoning) or bool(_THINKING_BLOCK.search(raw_text))
    # 文本化的工具调用（DSML）：工具并未真正执行，模型只是把调用协议写进了
    # content。若判成 ok，本轮会被当作完成申报，任务静默失败（用户只看到
    # 一堆标记）。单独成一类，由治理层按「响应质量问题」重试并给出纠偏提示。
    # 仅当剥掉标记后没有任何可见文字时才归此类——有实质正文的回合即使混入
    # 标记，也不该否定已给出的答复（标记会在落库出口被剔除）。
    if has_tool_markup(raw_text):
        visible = strip_tool_markup(strip_thinking(raw_text)).strip()
        if not visible:
            return "tool_markup"
        return "ok"

    visible = strip_thinking(raw_text)

    if not visible.strip() and not has_reasoning:
        return "empty"

    if has_reasoning and not visible.strip():
        return "reasoning"

    return "ok"


# ---------------------------------------------------------------------------
# result_truncation — re-exported from foundation/tool_output.py
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# batch_runtime
# ---------------------------------------------------------------------------


def _resolve_tool_batch_entries(
    tools: ToolRegistry,
    tool_calls: list[dict[str, Any]],
) -> list[tuple[str, str, EffectClass]]:
    from prismclaw.engine.toolkit.registry.tool_runtime import resolve_tool_effect_class

    resolved: list[tuple[str, str, EffectClass]] = []
    for call in tool_calls:
        call_id = call.get("id", "")
        function_payload = (call or {}).get("function") or {}
        tool_name = function_payload.get("name")
        if not isinstance(call_id, str) or not call_id:
            continue
        if not isinstance(tool_name, str) or not tool_name:
            continue
        tool = tools.get(tool_name)
        effect_class = (
            resolve_tool_effect_class(tool) if tool is not None else EffectClass.UNKNOWN_EFFECT
        )
        resolved.append((call_id, tool_name, effect_class))
    return resolved


def _failed_batch_signature(
    *,
    tools: ToolRegistry,
    tool_calls: list[dict[str, Any]],
    active_call_id: str | None,
) -> tuple[str, str, EffectClass] | None:
    resolved = _resolve_tool_batch_entries(tools, tool_calls)
    if not resolved:
        return None
    if len(resolved) == 1:
        return resolved[0]

    anchor_call_id = active_call_id or resolved[0][0]
    if all(tool_name == "sub_agent" for _call_id, tool_name, _effect_class in resolved):
        return anchor_call_id, "sub_agent_batch:parallel", EffectClass.UNKNOWN_EFFECT

    effect_classes = {effect_class for _call_id, _tool_name, effect_class in resolved}
    if effect_classes == {EffectClass.PURE_READ}:
        return anchor_call_id, "tool_batch:pure_read", EffectClass.PURE_READ
    if len(effect_classes) == 1:
        effect_class = next(iter(effect_classes))
        return anchor_call_id, f"tool_batch:{effect_class.value}", effect_class
    return anchor_call_id, "tool_batch:mixed", EffectClass.UNKNOWN_EFFECT


def _batch_effect_summary(effect_classes: list[EffectClass]) -> str:
    unique = list(dict.fromkeys(effect_classes))
    if not unique:
        return "unknown_effect"
    if len(unique) == 1:
        return unique[0].value
    return "mixed"


def _batch_runtime_kind(tool_names: list[str]) -> str:
    if tool_names and all(tool_name == "sub_agent" for tool_name in tool_names):
        return "subagent_parallel"
    return "tool_batch"


def _collect_batch_parallel_task_ids(results: list[ToolResult]) -> list[str]:
    task_ids: list[str] = []
    for result in results:
        envelope = result.subagent_envelope
        task_id = envelope.task_id if envelope is not None else None
        if isinstance(task_id, str) and task_id:
            task_ids.append(task_id)
    return list(dict.fromkeys(task_ids))


def _terminal_batch_status(
    *,
    tool_call_count: int,
    results: list[ToolResult],
) -> Literal["completed", "partial", "failed"]:
    error_count = sum(1 for result in results if result.is_error)
    member_count = max(tool_call_count, len(results))
    if error_count <= 0:
        return "completed"
    if member_count > 0 and error_count < member_count:
        return "partial"
    return "failed"
