"""todo_write tool — manage task checklist in session memory."""
from __future__ import annotations

from typing import Any

from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext, error_result, text_result
from prismclaw.schema.content.content_types import ToolResult
from prismclaw.schema.agent.session import TodoItem


class TodoWriteTool:
    name = "todo_write"
    description = "更新任务清单，每次调用都会整体替换列表。条目包含 id、content 和 status（pending/in_progress/completed）。"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "description": "完整的待办列表（替换现有列表）",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "唯一标识"},
                        "content": {"type": "string", "description": "任务描述"},
                        "status": {"type": "string", "description": "pending、in_progress 或 completed", "enum": ["pending", "in_progress", "completed"]},
                    },
                    "required": ["id", "content", "status"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["todos"],
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
        todos_data = args.get("todos", [])

        if not isinstance(todos_data, list):
            return error_result(call_id, "todo_write：'todos' 必须是数组")

        try:
            todo_items = [TodoItem(**t) for t in todos_data]
        except Exception as exc:
            return error_result(call_id, f"todo_write：无效的待办条目：{exc}")

        # Pydantic model attribute assignment (not dict-style)
        if hasattr(context, "session_state") and context.session_state is not None:
            context.session_state.todo_list = todo_items

        pending_count = sum(1 for t in todo_items if t.status == "pending")
        in_progress_count = sum(1 for t in todo_items if t.status == "in_progress")

        return text_result(
            call_id,
            f"todo_write：已更新 {len(todo_items)} 项（{pending_count} 项待处理，{in_progress_count} 项进行中）",
            metadata={
                "todos": [{"id": t.id, "content": t.content, "status": t.status} for t in todo_items],
                "pending_count": pending_count,
                "in_progress_count": in_progress_count,
            },
        )
