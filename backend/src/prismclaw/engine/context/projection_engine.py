from __future__ import annotations

from typing import cast

from prismclaw.engine.context.projection_models import ProjectionMessageSet, PromptProjection
from prismclaw.engine.state.projection_rendering import projected_context_block
from prismclaw.schema.content.context import ContextPackLayout
from prismclaw.schema.agent.session import AgentMessageDict, ProjectionInputMessage, SystemReminderProjection


def _render_reminder_body(reminder: SystemReminderProjection) -> str:
    if reminder.kind == "budget_warning":
        total_usd_cost = float(reminder.total_usd_cost or 0.0)
        budget_usd = float(reminder.budget_usd or 0.0)
        return (
            f"警告：你已消耗 ${total_usd_cost:.2f}，超过任务总预算（${budget_usd:.2f}）的 90%。"
            "请立即收尾当前任务并给出最终答复。"
        )
    if reminder.kind == "history_compaction_summary":
        return (
            "[系统提示：以下是为节省上下文窗口而压缩的早期对话摘要。]\n"
            f"{str(reminder.summary_text or '')}"
        )
    return str(reminder.text or "")


def render_system_reminder_content(reminder: SystemReminderProjection) -> str:
    return f"<system-reminder>\n{_render_reminder_body(reminder).rstrip()}\n</system-reminder>"


def _project_message(message: ProjectionInputMessage) -> AgentMessageDict:
    if isinstance(message, SystemReminderProjection):
        projected: AgentMessageDict = {
            "role": message.role,
            "content": render_system_reminder_content(message),
        }
        if message.entry_id is not None:
            projected["__entry_id__"] = message.entry_id
        return projected
    return cast(AgentMessageDict, dict(message))


class ContextProjectionEngine:
    def project(
        self,
        *,
        message_set: ProjectionMessageSet,
        turn_context_layout: ContextPackLayout,
    ) -> PromptProjection:
        projected_messages = [_project_message(message) for message in message_set.messages]
        projected_meta = [_project_message(message) for message in message_set.meta_messages]
        projected_appended = [_project_message(message) for message in message_set.appended_messages]
        return PromptProjection(
            system_blocks=[projected_context_block(block) for block in turn_context_layout.static_blocks],
            dynamic_projection=[projected_context_block(block) for block in turn_context_layout.dynamic_blocks],
            meta_messages=projected_meta,
            messages=projected_messages,
            appended_messages=projected_appended,
            history_projection=[projected_context_block(block) for block in turn_context_layout.history_blocks],
            tool_projection=[projected_context_block(block) for block in turn_context_layout.result_blocks],
        )
