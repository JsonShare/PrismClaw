from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

from prismclaw.engine.toolkit.registry.tool_runtime import (
    ToolContext,
    error_result,
    text_result,
)
from prismclaw.schema.content.content_types import (
    RuntimeControlArtifact,
    ToolContextModifier,
    ToolSkillExecutionStrategy,
    ToolSkillInvocation,
)
from prismclaw.schema.services.skills import (
    SkillInvocation,
    SkillInvocationState,
    normalize_skill_ref,
)

if TYPE_CHECKING:
    from prismclaw.engine.execution.skills.runtime import SkillRuntime

logger = logging.getLogger(__name__)


class ActivateSkillTool:
    """Activate a discovered skill from inside the agent loop.

    Skill discovery (``skill_search`` / the available-skills listing) only
    returns names and descriptions; without an explicit activation verb the
    model has no way to load a skill after the run has started (run-start
    auto-activation requires a unique gesture/match on the original user
    message).

    The tool builds the exact same :class:`SkillExecutionStrategy` as the
    preparer's designated/auto activation paths and returns it through the
    standard ``runtime_control`` channel, which the loop already knows how to
    consume:

    * ``inline`` skills -> ``context_modifier`` activates the invocation
      state, unlocks skill-gated tools and injects the SKILL.md instructions;
    * ``fork``/``subagent`` skills -> ``skill_execution_strategy`` is
      dispatched through the subagent runtime.
    """

    name = "activate_skill"
    description = (
        "用精确名称激活一个可用技能（名称可通过 skill_search 或可用技能列表获取），"
        "例如 'docx' 对应 Word 文档、'xlsx' 对应电子表格、'pptx' 对应幻灯片、"
        "'pdf' 对应 PDF。激活会加载该技能的分步指令，并为当前任务解锁其专用工具。"
        "确定合适技能后应立即调用本工具，然后遵循返回的指令。"
        "不要猜测技能名——只能使用展示给你的名称。"
    )
    tool_class = "read"
    parameters = {
        "type": "object",
        "properties": {
            "skill_name": {
                "type": "string",
                "description": (
                    "由 skill_search 或可用技能列表展示的精确技能名，例如 'docx'。"
                ),
            },
            "task_context": {
                "type": "string",
                "description": (
                    "可选的简短说明，用于描述激活该技能所服务的当前任务。"
                ),
            },
        },
        "required": ["skill_name"],
        "additionalProperties": False,
    }
    side_effect = False
    effect_class = "pure_read"

    def __init__(
        self,
        *,
        skill_runtime: SkillRuntime,
        capability_catalog: Any = None,
    ) -> None:
        self._skill_runtime = skill_runtime
        self._capability_catalog = capability_catalog

    async def execute(self, args: dict[str, Any], context: ToolContext):
        call_id = args.get("_call_id", "")
        raw_name = args.get("skill_name", "")
        if not isinstance(raw_name, str) or not raw_name.strip():
            return error_result(call_id, "缺少 'skill_name' 参数")
        skill_name = normalize_skill_ref(raw_name.strip())
        if not skill_name:
            return error_result(call_id, "缺少有效的 'skill_name' 参数")
        raw_task = args.get("task_context", "")
        task_context = raw_task.strip() if isinstance(raw_task, str) else ""

        # Idempotency: re-activating the skill that is already active must not
        # re-inject the full SKILL.md (context bloat) — the model often retries
        # activation when it does not immediately know what to do next.
        active_state = _coerce_active_skill_state(context)
        if active_state is not None and active_state.skill_name == skill_name:
            return text_result(
                call_id,
                f"技能“{skill_name}”已处于激活状态，其指令已注入——"
                "请直接遵循这些指令，不要重复激活。"
                "请执行规定的步骤（读取引用的文件、用绝对路径运行命令），"
                "并在交付物产出后调用 present_files。",
            )

        invocation = SkillInvocation(
            skill_name=skill_name,
            task_context=task_context,
            source="model",
        )
        try:
            plan = self._skill_runtime.build_skill_execution(
                invocation,
                workspace_path=context.workspace_path,
                parent_active_state=active_state,
            )
        except LookupError as exc:
            return error_result(call_id, str(exc))
        except Exception:
            logger.warning(
                "activate_skill failed: skill=%s session=%s",
                skill_name,
                context.session_id,
                exc_info=True,
            )
            return error_result(
                call_id,
                f"技能“{skill_name}”因内部错误无法激活。"
                "请用 skill_search 确认精确名称，或改用通用工具完成任务。",
            )

        skill_invocation = ToolSkillInvocation(
            skill_name=plan.skill_name,
            task_context=task_context or plan.summary,
            source="model",
            args={"activated_via": "activate_skill"},
        )

        if plan.execution_mode == "inline":
            runtime_control = RuntimeControlArtifact(
                skill_name=plan.skill_name,
                skill_summary=plan.summary,
                skill_invocation=skill_invocation,
                context_modifier=ToolContextModifier(
                    active_skill_name=plan.skill_name,
                    allowed_tools_override=list(plan.allowed_tools)
                    if plan.allowed_tools is not None
                    else None,
                    append_messages=[dict(m) for m in plan.append_messages],
                ),
            )
            message = (
                f"技能“{plan.skill_name}”已激活。\n\n"
                "请严格遵循已注入上下文的技能指令，使用其规定的工具，"
                "并在交付物完成后用 present_files 工具展示。"
            )
        else:
            runtime_control = RuntimeControlArtifact(
                skill_name=plan.skill_name,
                skill_summary=plan.summary,
                skill_invocation=skill_invocation,
                skill_execution_strategy=ToolSkillExecutionStrategy(
                    skill_name=plan.skill_name,
                    execution_mode=plan.execution_mode,
                    allowed_tools=list(plan.allowed_tools)
                    if plan.allowed_tools is not None
                    else None,
                    preload_skills=list(plan.preload_skills),
                    subagent_manifest=plan.subagent_manifest,
                    append_messages=[dict(m) for m in plan.append_messages],
                ),
            )
            message = (
                f"技能“{plan.skill_name}”正以“{plan.execution_mode}”执行模式分发。"
            )

        return text_result(call_id, message, runtime_control=runtime_control)


def _coerce_active_skill_state(
    context: ToolContext,
) -> SkillInvocationState | None:
    """Return the currently ACTIVE skill invocation state, if any.

    Handles both ToolContext payload shapes:
      * snapshot dump — ``{"active_invocation": {...}, ...}`` (see
        ``InvokedSkillState``);
      * discovery/runtime payload — ``{"active_runtime": {...}}``.
    Mirrors :func:`prismclaw.engine.toolkit.registry.discovery._coerce_skill_invocation`.
    Only a standard, ``is_active`` skill invocation counts; a command
    dispatch (``invocation_context="command"``) does not lock activation.
    """
    raw_state = context.invoked_skill_state
    state: SkillInvocationState | None = None
    if isinstance(raw_state, SkillInvocationState):
        state = raw_state
    elif isinstance(raw_state, dict):
        for key in ("active_invocation", "active_runtime"):
            raw = raw_state.get(key)
            if isinstance(raw, SkillInvocationState):
                state = raw
                break
            if isinstance(raw, dict):
                try:
                    state = SkillInvocationState.model_validate(raw)
                    break
                except Exception:
                    state = None
    if state is None:
        return None
    if state.is_active and state.invocation_context == "skill":
        return state
    return None
