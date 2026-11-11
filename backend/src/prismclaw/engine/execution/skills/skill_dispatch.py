from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Any, cast, TYPE_CHECKING

from prismclaw.engine.subagent.definition.manifest import AgentManifest
from prismclaw.engine.toolkit.registry.registry import ToolRegistry
from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext
from prismclaw.engine.compression.preservation import project_active_skill_invocation
from prismclaw.engine.execution.checkpoint import (
    InvokedSkillRecord,
    RunSnapshot,
)
from prismclaw.schema.agent.data_types import TextBlock
from prismclaw.schema.content.content_types import RuntimeControlArtifact, ToolContextModifier, ToolResult, ToolSkillExecutionStrategy
from prismclaw.schema.agent.session import AgentMessageDict, ProjectionInputMessage, SystemReminderProjection
from prismclaw.schema.services.skills import SkillInvocationState

if TYPE_CHECKING:
    from prismclaw.engine.execution.loop.bootstrap import AgentRunnerDeps
    from prismclaw.engine.execution.loop.runner import RunRequest

logger = logging.getLogger(__name__)


def _coerce_appended_messages(raw: Any) -> list[ProjectionInputMessage]:
    if not isinstance(raw, list):
        return []
    out: list[ProjectionInputMessage] = []
    for item in raw:
        if isinstance(item, dict):
            out.append(cast(AgentMessageDict, dict(item)))
    return out


def _merge_runtime_control(
    *,
    original: RuntimeControlArtifact | None,
    dispatched: RuntimeControlArtifact | None,
) -> RuntimeControlArtifact | None:
    if original is None and dispatched is None:
        return None
    if dispatched is None:
        return original
    if original is None:
        return dispatched
    return dispatched.model_copy(
        update={
            "skill_name": dispatched.skill_name or original.skill_name,
            "skill_summary": dispatched.skill_summary or original.skill_summary,
            "skill_invocation": dispatched.skill_invocation or original.skill_invocation,
            "skill_execution_strategy": dispatched.skill_execution_strategy
            or original.skill_execution_strategy,
            "context_modifier": dispatched.context_modifier or original.context_modifier,
        }
    )


def _append_control_message_override(
    messages_override: list[ProjectionInputMessage] | None,
    message: ProjectionInputMessage,
) -> list[ProjectionInputMessage]:
    copied_message: ProjectionInputMessage
    if isinstance(message, SystemReminderProjection):
        copied_message = message
    else:
        copied_message = cast(AgentMessageDict, dict(message))
    if messages_override is None:
        return [copied_message]
    return [*messages_override, copied_message]


def _normalize_allowed_tool_names(
    registry: ToolRegistry,
    raw: list[str] | None,
) -> list[str] | None:
    if raw is None:
        return None

    registered = registry.names()
    lower_map: dict[str, str] = {n.lower(): n for n in registered}

    normalized: list[str] = []
    dropped: list[str] = []

    for item in raw:
        tool = registry.get(item)
        if tool is not None:
            if tool.name not in normalized:
                normalized.append(tool.name)
            continue

        lower_item = item.lower()
        if lower_item in lower_map:
            canonical = lower_map[lower_item]
            if canonical not in normalized:
                normalized.append(canonical)
            logger.info(
                "allowed_tools: case-insensitive match %r → %r",
                item, canonical,
            )
            continue

        dropped.append(item)

    if dropped:
        logger.warning(
            "allowed_tools: dropped unregistered names: %s",
            ", ".join(dropped),
        )

    if not normalized:
        logger.warning(
            "allowed_tools: all names invalid, falling back to None (unrestricted)",
        )
        return None

    return normalized


def _rendered_skill_prompt_fragment(modifier: ToolContextModifier | None) -> str:
    if modifier is None:
        return ""
    fragments: list[str] = []
    for message in _coerce_appended_messages(modifier.append_messages):
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            fragments.append(content.strip())
    return "\n\n".join(fragments)


def _record_invoked_skill_from_runtime_control(
    *,
    deps: AgentRunnerDeps,
    request: RunRequest,
    snapshot: RunSnapshot,
    runtime_control: RuntimeControlArtifact | None,
) -> None:
    if runtime_control is None:
        return
    modifier = runtime_control.context_modifier
    plan = runtime_control.skill_execution_strategy
    if isinstance(runtime_control.skill_name, str) and runtime_control.skill_name:
        skill_name = runtime_control.skill_name
    elif modifier is not None:
        skill_name = modifier.active_skill_name
    else:
        return
    catalog_entry = deps.capability_catalog.get_skill_entry(skill_name)
    instructions = catalog_entry.instructions if catalog_entry is not None else ""
    snapshot.invoked_skill_state.skills[skill_name] = InvokedSkillRecord(
        skill_name=skill_name,
        skill_path=catalog_entry.location if catalog_entry is not None else "",
        content_digest=hashlib.sha1(instructions.encode("utf-8")).hexdigest()[:16]
        if instructions
        else "",
        rendered_prompt_fragment=_rendered_skill_prompt_fragment(modifier),
        agent_id=request.agent_id,
        invoked_at_epoch_ms=int(time.time() * 1000),
        execution_mode=plan.execution_mode if plan is not None else "inline",
        allowed_tools=(
            list(plan.allowed_tools)
            if plan is not None and isinstance(plan.allowed_tools, list)
            else (
                list(modifier.allowed_tools_override)
                if modifier is not None and isinstance(modifier.allowed_tools_override, list)
                else None
            )
        ),
        preload_skills=list(plan.preload_skills) if plan is not None else [],
        clear_after_terminal_result=(
            modifier.clear_after_terminal_result if modifier is not None else True
        ),
    )


def _skill_dispatch_error_result(
    *,
    call_id: str,
    message: str,
    runtime_control: RuntimeControlArtifact | None,
) -> ToolResult:
    return ToolResult(
        tool_call_id=call_id,
        content=[TextBlock(text=message)],
        is_error=True,
        runtime_control=runtime_control,
    )


async def _dispatch_skill_execution_strategy_result(
    *,
    deps: AgentRunnerDeps,
    tool_ctx: ToolContext,
    result: ToolResult,
    plan: ToolSkillExecutionStrategy,
    runtime_control: RuntimeControlArtifact | None,
) -> ToolResult:
    subagent_tool = deps.tools.get("sub_agent")
    if subagent_tool is None:
        return _skill_dispatch_error_result(
            call_id=result.tool_call_id,
            message=(
                f"技能执行模式 '{plan.execution_mode}' 需要 sub_agent 运行时，"
                "但当前 sub_agent 工具不可用。"
            ),
            runtime_control=runtime_control,
        )
    registry = getattr(subagent_tool, "registry", None)
    if registry is None or not hasattr(registry, "register"):
        return _skill_dispatch_error_result(
            call_id=result.tool_call_id,
            message=(
                f"技能执行模式 '{plan.execution_mode}' 需要可写入的子代理注册表，"
                "但当前运行时不提供该能力。"
            ),
            runtime_control=runtime_control,
        )
    if not isinstance(plan.subagent_manifest, dict):
        return _skill_dispatch_error_result(
            call_id=result.tool_call_id,
            message=(
                f"技能执行模式 '{plan.execution_mode}' 未提供子代理清单数据。"
            ),
            runtime_control=runtime_control,
        )
    try:
        manifest = AgentManifest.model_validate(plan.subagent_manifest)
    except Exception:
        logger.debug("invalid subagent manifest in skill_execution_strategy", exc_info=True)
        return _skill_dispatch_error_result(
            call_id=result.tool_call_id,
            message="技能执行计划中包含无效的子代理清单。",
            runtime_control=runtime_control,
        )
    registry.register(manifest.name, manifest)
    task = ""
    invocation = runtime_control.skill_invocation if runtime_control is not None else None
    if invocation is not None:
        task = invocation.task_context.strip()
    if not task:
        task = f"执行技能 '{plan.skill_name}'。"
    dispatch_args: dict[str, Any] = {
        "_call_id": result.tool_call_id,
        "agent_name": manifest.name,
        "task": task,
    }
    if invocation is not None and "context_snapshot" in invocation.args:
        dispatch_args["context_snapshot"] = invocation.args.get("context_snapshot")
    dispatched = await subagent_tool.execute(dispatch_args, tool_ctx)
    merged_runtime_control = _merge_runtime_control(
        original=runtime_control,
        dispatched=dispatched.runtime_control,
    )
    return dispatched.model_copy(update={"runtime_control": merged_runtime_control})


async def _execute_skill_execution_strategys_from_results(
    *,
    deps: AgentRunnerDeps,
    tool_ctx: ToolContext,
    results: list[ToolResult],
) -> list[ToolResult]:
    resolved: list[ToolResult] = []
    for result in results:
        runtime_control = result.runtime_control
        plan = runtime_control.skill_execution_strategy if runtime_control is not None else None
        if plan is None or plan.execution_mode == "inline":
            resolved.append(result)
            continue
        resolved.append(
            await _dispatch_skill_execution_strategy_result(
                deps=deps,
                tool_ctx=tool_ctx,
                result=result,
                plan=plan,
                runtime_control=runtime_control,
            )
        )
    return resolved


@dataclass(frozen=True)
class ContextInjection:
    """一次技能激活（或切换）注入模型上下文的记录。

    kind='context' cell 的数据源：技能 prompt 本体进系统提示（由 system
    cell 逐轮快照覆盖），这里携带的是激活时点注入的控制消息文本。
    """

    skill_name: str
    content: str
    entry_id: str


def _apply_context_modifiers_from_results(
    *,
    deps: AgentRunnerDeps,
    request: RunRequest,
    snapshot: RunSnapshot,
    tool_ctx: ToolContext,
    results: list[ToolResult],
    skill_invocation: SkillInvocationState | None,
    messages_override: list[ProjectionInputMessage] | None,
) -> tuple[
    SkillInvocationState | None,
    list[ProjectionInputMessage] | None,
    list[ContextInjection],
]:
    resolved_state = skill_invocation
    updated_messages = messages_override
    injections: list[ContextInjection] = []
    for result in results:
        runtime_control = result.runtime_control
        _record_invoked_skill_from_runtime_control(
            deps=deps,
            request=request,
            snapshot=snapshot,
            runtime_control=runtime_control,
        )
        modifier = runtime_control.context_modifier if runtime_control is not None else None
        if modifier is None:
            continue
        old_skill_name = (
            resolved_state.skill_name if resolved_state is not None else None
        )
        if (
            old_skill_name is not None
            and modifier.active_skill_name != old_skill_name
        ):
            logger.warning(
                "skill_override old=%s new=%s",
                old_skill_name,
                modifier.active_skill_name,
            )
            switch_msg: ProjectionInputMessage = {
                "role": "user",
                "content": (
                    f"[已从技能“{old_skill_name}”切换到技能“{modifier.active_skill_name}”。"
                    "此前技能的约束不再生效。]"
                ),
            }
            updated_messages = _append_control_message_override(
                updated_messages, switch_msg
            )
        catalog_entry = (
            deps.capability_catalog.get_skill_entry(modifier.active_skill_name)
            if deps.capability_catalog is not None
            else None
        )
        plugin_name = getattr(catalog_entry, "plugin_name", None) if catalog_entry is not None else None
        resolved_state = SkillInvocationState(
            skill_name=modifier.active_skill_name,
            allowed_tools_override=_normalize_allowed_tool_names(
                deps.tools, modifier.allowed_tools_override
            ),
            is_active=True,
            clear_after_terminal_result=modifier.clear_after_terminal_result,
            plugin_name=plugin_name,
            invocation_context=modifier.invocation_context,
        )
        injected_texts: list[str] = []
        for message in _coerce_appended_messages(modifier.append_messages):
            updated_messages = _append_control_message_override(updated_messages, message)
            content_value = message.get("content") if isinstance(message, dict) else None
            if isinstance(content_value, str) and content_value:
                injected_texts.append(content_value)
        # 只在技能实际变化（激活/切换）时记录 context 注入 —— 同技能的
        # 重复断言不产生新上下文，避免每工具结果刷一条 context cell
        if old_skill_name != modifier.active_skill_name:
            content = "\n\n".join(injected_texts)
            injections.append(ContextInjection(
                skill_name=modifier.active_skill_name,
                content=content,
                entry_id=(
                    f"skill_{modifier.active_skill_name}_{int(time.time() * 1000)}"
                ),
            ))
    project_active_skill_invocation(
        request=request,
        snapshot=snapshot,
        tool_ctx=tool_ctx,
        skill_invocation=resolved_state,
    )
    return resolved_state, updated_messages, injections