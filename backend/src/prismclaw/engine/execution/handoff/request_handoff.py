from __future__ import annotations

import re
import unicodedata
import uuid
from typing import Any

from prismclaw.engine.execution.capabilities.catalog import CapabilityCatalogService
from prismclaw.engine.execution.handoff.contract import build_handoff_request_payload
from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext
from prismclaw.schema.content.content_types import ToolResult
from prismclaw.schema.agent.data_types import TextBlock

_TARGET_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")
_TASK_MIN_CHARS = 8
_TASK_MAX_CHARS = 8192
_CONTEXT_HINT_MAX_CHARS = 16384
_TASK_MIN_DISTINCT_CHARS = 4

_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "target": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9-]*$",
            "description": (
                "接续本次任务的下一子代理名称。仅限小写字母与连字符，且以字母开头。"
                "运行时会对注册表以及你所在清单的 can_handoff_to 白名单（如已设置）进行校验。"
            ),
        },
        "task": {
            "type": "string",
            "minLength": _TASK_MIN_CHARS,
            "maxLength": _TASK_MAX_CHARS,
            "description": (
                "交给下一子代理的任务描述（8–8192 个字符）。请尽量具体；下一代理会将其作为初始用户消息接收。"
            ),
        },
        "context_hint": {
            "type": "string",
            "maxLength": _CONTEXT_HINT_MAX_CHARS,
            "description": (
                "可选的补充上下文（≤16384 个字符），用于帮助下一代理"
                "（例如需要重点关注的路径、此前的决策）。会与任务描述一同传递。"
            ),
        },
    },
    "required": ["target", "task"],
    "additionalProperties": False,
}

_DESCRIPTION = (
    "声明结构化的移交意图：当本子代理已完成主要任务、而下一步应归属另一个子代理时，"
    "调用 request_handoff(target=<name>, task=<description>, context_hint=<optional>)。"
    "调用成功后子代理循环会在此结束，由监督者决定是否把任务派发给建议的目标。"
    "仅限子代理使用；调用会去重（首次尝试生效，无论成功或失败）。"
)


class RequestHandoffTool:
    name = "request_handoff"
    side_effect = False
    tool_class = "read"
    visibility_scope = "subagent_only"

    def __init__(self) -> None:
        self.parameters = _PARAMETERS_SCHEMA
        self.description = _DESCRIPTION

    async def execute(
        self, args: dict[str, Any], context: ToolContext
    ) -> ToolResult:
        call_id = args.get("_call_id", "")
        if context.subagent_depth <= 0:
            return _err(
                call_id,
                "request_handoff 仅供子代理使用；父代理请直接派发 sub_agent",
            )
        target = args.get("target", "")
        task = args.get("task", "")
        context_hint = args.get("context_hint")

        format_error = _validate_format(target, task, context_hint)
        if format_error is not None:
            return _err(call_id, format_error)

        if context.handoff_request_seen:
            return _err(
                call_id,
                "本轮循环中已尝试过 request_handoff；"
                "首次尝试生效（无论成功或失败）",
            )

        context.handoff_request_seen = True

        if context.abort.is_set() or context.subagent_timed_out:
            context.pending_handoff = None
            return _err(
                call_id,
                "request_handoff 已丢弃：子代理正在中止或已超时",
            )

        catalog = context.subagent_catalog
        manifest = context.subagent_manifest

        if catalog is not None and isinstance(catalog, CapabilityCatalogService):
            available_names = [entry.name for entry in catalog.list_agent_entries()]
            if target not in available_names:
                return _err(
                    call_id,
                    f"移交目标 {target!r} 不存在，"
                    f"可用目标：{', '.join(available_names) or '（无）'}",
                )

        if manifest is not None and getattr(manifest, "can_handoff_to", None):
            allowed = manifest.can_handoff_to
            if target not in allowed:
                source_name = getattr(manifest, "name", "<unknown>")
                return _err(
                    call_id,
                    f"{target!r} 不在 {source_name} 的移交白名单中，"
                    f"允许的目标：{', '.join(allowed)}",
                )

        source_subagent = (
            getattr(manifest, "name", None) if manifest is not None else None
        )
        if source_subagent is None:
            source_subagent = context.subagent_name or "<unknown>"
        skill_origin = (
            getattr(manifest, "skill_origin", None) if manifest is not None else None
        )
        preload_skills = (
            getattr(manifest, "declared_invoke_sequence", None) if manifest is not None else None
        )
        is_fork = (
            getattr(manifest, "execution_mode", "sync") == "parallel"
            if manifest is not None
            else False
        )

        source_chain_id = context.task_runtime_ctx.handoff_chain_id or uuid.uuid4().hex
        source_hop_index = context.task_runtime_ctx.handoff_hop_index or 0
        source_effective_hop_index = context.task_runtime_ctx.effective_hop_index
        if source_effective_hop_index is None:
            source_effective_hop_index = source_hop_index

        context.pending_handoff = build_handoff_request_payload(
            target=target,
            task=task,
            context_hint=context_hint,
            source_subagent=source_subagent,
            source_sub_session_id=context.session_id,
            source_chain_id=source_chain_id,
            source_hop_index=source_hop_index,
            source_effective_hop_index=source_effective_hop_index,
            source_skill_origin=skill_origin,
            source_preload_skills=preload_skills,
            source_is_fork=is_fork,
        )
        context.should_break_after_results = True

        return ToolResult(
            tool_call_id=call_id,
            content=[
                TextBlock(
                    text=f"已排队移交至 {target}；子代理循环到此结束。"
                )
            ],
            is_error=False,
        )


def _validate_format(
    target: str, task: str, context_hint: str | None
) -> str | None:
    if not target or not _TARGET_PATTERN.match(target):
        return (
            f"request_handoff 的 target 必须匹配 ^[a-z][a-z0-9-]*\\$ "
            f"（仅小写字母与连字符，且以字母开头）；实际收到 {target!r}"
        )
    task_norm = unicodedata.normalize("NFC", task)
    n = len(task_norm)
    if n < _TASK_MIN_CHARS:
        return (
            f"request_handoff 的 task 至少需要 {_TASK_MIN_CHARS} 个字符；"
            f"实际收到 {n}"
        )
    if n > _TASK_MAX_CHARS:
        return (
            f"request_handoff 的 task 不得超过 {_TASK_MAX_CHARS} 个字符；"
            f"实际收到 {n}"
        )
    if len(set(task_norm)) < _TASK_MIN_DISTINCT_CHARS:
        return (
            f"request_handoff 的 task 至少需包含 "
            f"{_TASK_MIN_DISTINCT_CHARS} 个不同字符；"
            f"实际收到 {len(set(task_norm))}"
        )
    if context_hint is not None:
        hint_n = len(unicodedata.normalize("NFC", context_hint))
        if hint_n > _CONTEXT_HINT_MAX_CHARS:
            return (
                f"request_handoff 的 context_hint 不得超过 {_CONTEXT_HINT_MAX_CHARS} "
                f"个字符；实际收到 {hint_n}"
            )
    return None


def _err(call_id: str, message: str) -> ToolResult:
    return ToolResult(
        tool_call_id=call_id,
        content=[TextBlock(text=message)],
        is_error=True,
    )


__all__ = ["RequestHandoffTool"]
