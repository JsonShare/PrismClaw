"""Goal management tool — create and track long-running goals."""
from __future__ import annotations

import uuid
from typing import Any

from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext, error_result, text_result
from prismclaw.schema.content.content_types import ToolResult
from prismclaw.schema.agent.session import GoalRecord

_GOAL_ACTIONS = ("create", "edit", "pause", "resume", "complete", "blocked")


class GoalTool:
    name = "goal"
    description = (
        "创建与管理目标。目标会跨轮次持久保存，并跟踪推进进度。\n"
        "动作：create（新建目标，需提供 objective）——返回 goal_id；"
        "edit（重写目标描述）、pause、resume、complete、blocked（均需提供 goal_id）。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": list(_GOAL_ACTIONS),
                "description": "create：新建目标；edit/pause/resume/complete/blocked：更新已有目标。",
            },
            "objective": {
                "type": "string",
                "description": "目标描述（create 时必填；edit 时为新的文本）。",
            },
            "goal_id": {
                "type": "string",
                "description": "目标 ID（除 create 外，每个动作都必填）。",
            },
            "max_goal_rounds": {
                "type": "integer",
                "description": "目标被视为受阻前允许的最大轮数（默认 20）。",
                "default": 20,
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    }
    side_effect = False
    tool_class = "memory-write-safe"
    effect_class = "local_mutation"
    approval_eligible = False
    is_concurrency_safe = True
    timeout_seconds = 10.0

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        call_id = args.get("_call_id", "")
        action = args.get("action")
        if action not in _GOAL_ACTIONS:
            allowed = ", ".join(_GOAL_ACTIONS)
            return error_result(call_id, f"goal：无效的 action '{action}'（允许值：{allowed}）")
        if action == "create":
            return self._create(call_id, args, context)
        return self._update(call_id, args, action, context)

    # ── create ─────────────────────────────────────────────────────────

    def _create(self, call_id: str, args: dict[str, Any], context: ToolContext) -> ToolResult:
        objective = args.get("objective")
        if not isinstance(objective, str) or not objective:
            return error_result(call_id, "goal：create 操作需要 'objective'")

        max_rounds = args.get("max_goal_rounds", 20)
        try:
            max_rounds = int(max_rounds)
        except (TypeError, ValueError):
            max_rounds = 20

        goal_id = f"g_{uuid.uuid4().hex[:8]}"
        goal = GoalRecord(
            goal_id=goal_id,
            objective=objective,
            max_goal_rounds=max_rounds,
        )

        if hasattr(context, "session_state") and context.session_state is not None:
            context.session_state.goals.append(goal)

        return text_result(
            call_id,
            f"goal：已创建目标 {goal_id}：{objective}",
            metadata={"goal_id": goal_id, "objective": objective, "max_goal_rounds": max_rounds},
        )

    # ── update ─────────────────────────────────────────────────────────

    def _update(
        self,
        call_id: str,
        args: dict[str, Any],
        action: str,
        context: ToolContext,
    ) -> ToolResult:
        goal_id = args.get("goal_id")
        if not isinstance(goal_id, str) or not goal_id:
            return error_result(call_id, f"goal：{action} 操作需要 'goal_id'")

        if not hasattr(context, "session_state") or context.session_state is None:
            return error_result(call_id, "goal：当前没有可用的会话状态")

        goal = None
        for g in context.session_state.goals:
            if g.goal_id == goal_id:
                goal = g
                break

        if goal is None:
            return error_result(call_id, f"goal：未找到目标 {goal_id}")

        if action == "edit":
            new_objective = args.get("objective")
            if not isinstance(new_objective, str) or not new_objective:
                return error_result(call_id, "goal：edit 操作需要 'objective'")
            goal.objective = new_objective
        elif action == "pause":
            goal.status = "paused"
        elif action == "resume":
            goal.status = "active"
            goal.current_round += 1
        elif action == "complete":
            goal.status = "completed"
        elif action == "blocked":
            goal.status = "blocked"
            goal.blocked_count += 1

        return text_result(
            call_id,
            f"goal：已在 {goal_id} 上执行 {action}",
            metadata={"goal_id": goal_id, "action": action, "objective": goal.objective if action == "edit" else None},
        )