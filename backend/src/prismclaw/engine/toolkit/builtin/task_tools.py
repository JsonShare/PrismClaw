from __future__ import annotations

from typing import Any, cast

from prismclaw.engine.subagent.runtime.follow_up import (
    SessionFollowUpSnapshot,
    format_parallel_task_attention,
    format_parallel_task_summary,
    session_follow_up_snapshot,
)
from prismclaw.engine.subagent.runtime.parallel import (
    ParallelTaskRuntimeRecord,
    latest_parallel_task_for_tree,
)
from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext, error_result, text_result
from prismclaw.schema.agent.task_types import TaskInfo


# ─── Task helper functions ────────────────────────────────────────────

def _task_info_to_metadata(task: TaskInfo) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "task_id": task.task_id,
        "name": task.name,
        "category": task.category,
        "state": task.state,
        "created_at_monotonic": round(task.created_at, 6),
        "duration_seconds": round(task.duration_s, 3),
    }
    if task.owner is not None:
        metadata["owner"] = task.owner
    if task.exception is not None:
        metadata["exception"] = task.exception
    if task.result_payload is not None:
        metadata["result_payload"] = task.result_payload
    return metadata


def _task_projection_for_context(
    context: ToolContext,
    *,
    task_id: str,
) -> dict[str, Any] | None:
    session_tree = context.session_tree
    if session_tree is None:
        return None
    getter = getattr(session_tree, "get_latest_fork_task_projection", None)
    if not callable(getter):
        return None
    projection = getter(task_id=task_id)
    return dict(projection) if isinstance(projection, dict) else None


def _parallel_task_for_context(
    context: ToolContext,
    *,
    task_id: str,
) -> ParallelTaskRuntimeRecord | None:
    session_tree = context.session_tree
    if session_tree is None:
        return None
    return latest_parallel_task_for_tree(session_tree, task_id=task_id)


def _parallel_task_to_metadata(record: ParallelTaskRuntimeRecord) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "task_id": record.task_id,
        "name": record.agent_name or record.kind,
        "category": record.kind,
        "state": record.status,
        "owner": record.owner_session_id,
        "notification_state": record.notification_state,
        "projected_to_session": False,
    }
    if record.result_summary is not None:
        metadata["result_summary"] = record.result_summary
    if record.error_summary is not None:
        metadata["error_summary"] = record.error_summary
    if record.projection_payload:
        metadata["projection_payload"] = dict(record.projection_payload)
    if record.managed_task_id is not None:
        metadata["managed_task_id"] = record.managed_task_id
    return metadata


def _managed_task_for_context(
    context: ToolContext,
    *,
    task_id: str,
) -> TaskInfo | None:
    task_manager = context.task_manager
    list_tasks = getattr(task_manager, "list_tasks", None) if task_manager is not None else None
    if not callable(list_tasks):
        return None
    owner = context.session_id
    tasks = cast(list[TaskInfo], list_tasks(owner=owner, include_done=True))
    return next((task for task in tasks if task.task_id == task_id), None)


def _session_follow_up_snapshot_for_context(
    context: ToolContext,
) -> SessionFollowUpSnapshot:
    session_tree = context.session_tree
    if session_tree is None:
        return SessionFollowUpSnapshot()
    return session_follow_up_snapshot(session_tree)


def _task_metadata_with_projection(task: TaskInfo, context: ToolContext) -> dict[str, Any]:
    metadata = _task_info_to_metadata(task)
    projection = _task_projection_for_context(context, task_id=task.task_id)
    if projection is not None:
        metadata["fork_task_projection"] = projection
        metadata["projected_to_session"] = True
    else:
        metadata["projected_to_session"] = False
    return metadata


def _parallel_task_metadata_with_projection(
    record: ParallelTaskRuntimeRecord,
    context: ToolContext,
) -> dict[str, Any]:
    metadata = _parallel_task_to_metadata(record)
    projection = _task_projection_for_context(context, task_id=record.task_id)
    if projection is not None:
        metadata["fork_task_projection"] = projection
        metadata["projected_to_session"] = True
    managed_task_id = record.managed_task_id
    if isinstance(managed_task_id, str) and managed_task_id:
        managed_task = _managed_task_for_context(context, task_id=managed_task_id)
        if managed_task is not None:
            metadata["managed_task"] = _task_info_to_metadata(managed_task)
    return metadata


def _format_task_summary(task: TaskInfo, *, projection: dict[str, Any] | None = None) -> str:
    summary = (
        f"Task {task.task_id}: {task.name} | category={task.category} | state={task.state}"
        f" | duration={task.duration_s:.3f}s"
    )
    if task.exception:
        summary += f"\nException: {task.exception}"
    if task.result_payload is not None:
        summary += "\nResult payload available."
    if projection is not None:
        summary += "\nSession projection available."
    return summary


def _format_parallel_task_summary(
    record: ParallelTaskRuntimeRecord,
    *,
    projection: dict[str, Any] | None = None,
) -> str:
    summary = (
        f"Task {record.task_id}: {record.agent_name or record.kind}"
        f" | category={record.kind} | state={record.status}"
        f" | notification={record.notification_state}"
    )
    if record.error_summary:
        summary += f"\nError: {record.error_summary}"
    if record.result_summary:
        summary += "\nResult summary available."
    if projection is not None:
        summary += "\nSession projection available."
    return summary


# ─── CancelTaskTool ───────────────────────────────────────────────────

class CancelTaskTool:
    name = "cancel_task"
    description = (
        "取消由 bash 或 python 工具启动的后台任务。"
        "请提供启动该任务的工具响应中返回的 task_id。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "要取消的后台任务 ID，由启动该任务的工具返回。",
            },
        },
        "required": ["task_id"],
        "additionalProperties": False,
    }
    side_effect = True
    is_concurrency_safe = False  # cancels running tasks, mutation
    tool_class = "write"
    effect_class = "local_mutation"
    skill_gated = True  # 任务取消已改为需任务管理 Skill 激活时可见

    async def execute(self, args: dict[str, Any], context: ToolContext) -> Any:

        call_id = args.get("_call_id", "")
        raw_task_id = args.get("task_id")
        task_id = raw_task_id.strip() if isinstance(raw_task_id, str) else ""
        if not task_id:
            return error_result(call_id, "cancel_task：缺少必需参数 'task_id'。")

        parallel_task_store = (
            context.parallel_task_store
        )
        if parallel_task_store is None:
            return error_result(
                call_id,
                "cancel_task：当前会话中并行任务运行时不可用。",
            )

        record = await parallel_task_store.get(
            session_id=context.session_id,
            task_id=task_id,
        )
        if record is None:
            return error_result(
                call_id,
                f"cancel_task：当前会话中未找到并行任务 {task_id!r}。",
            )

        managed_task_id = record.managed_task_id
        task_manager = context.task_manager
        cancel_managed = getattr(task_manager, "cancel", None) if task_manager is not None else None
        if isinstance(managed_task_id, str) and managed_task_id and callable(cancel_managed):
            await cancel_managed(managed_task_id)

        cancelled = await parallel_task_store.cancel(
            session_id=context.session_id,
            task_id=task_id,
            error_summary="cancelled_by_user",
        )
        if cancelled is None:
            return error_result(
                call_id,
                f"cancel_task：并行任务 {task_id!r} 无法取消。",
            )

        projection = _task_projection_for_context(context, task_id=task_id)
        follow_up_snapshot = _session_follow_up_snapshot_for_context(context)
        background_summary = follow_up_snapshot.parallel_task_summary
        background_attention = follow_up_snapshot.parallel_task_attention
        metadata: dict[str, Any] = {
            "task": _parallel_task_metadata_with_projection(cancelled, context),
        }
        if background_summary is not None:
            metadata["parallel_task_summary"] = background_summary
        if background_attention is not None:
            metadata["parallel_task_attention"] = background_attention
        text = _format_parallel_task_summary(cancelled, projection=projection)
        if background_summary is not None:
            text += "\n" + format_parallel_task_summary(background_summary)
        if background_attention is not None:
            text += "\n" + format_parallel_task_attention(background_attention)
        return text_result(call_id, text, metadata=metadata)


# ─── DeclareBlockedTool ───────────────────────────────────────────────

class DeclareBlockedTool:
    name = "declare_blocked"
    description = (
        "声明任务目标确实已被阻塞（例如缺少凭据、外部依赖不可达、硬性约束相互冲突）。"
        "请提供稳定的 reason_code，以及具体说明阻塞情况的 reason_message。"
        "声明阻塞本身并不会结束任务：运行循环只会在足够多的推进轮次之后接受该声明，"
        "然后带着你给出的原因终止。任务难度大、存在不确定性、仍有可做的工作、"
        "或预算即将耗尽，均不属于阻塞条件。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "reason_code": {
                "type": "string",
                "description": "稳定的机器可读代码，例如 'missing_credentials' 或 'external_service_unreachable'。",
            },
            "reason_message": {
                "type": "string",
                "description": "具体的阻塞情况：尝试了什么，以及究竟是什么导致无法完成。",
            },
        },
        "required": ["reason_code", "reason_message"],
        "additionalProperties": False,
    }
    side_effect = False
    is_concurrency_safe = True
    tool_class = "read"
    effect_class = "pure_read"
    # 阻塞申报由主循环的完成判定消费；子代理循环没有该裁决点，
    # 暴露给它只会产出"已记录"的空指引。
    visibility_scope = "main_only"

    async def execute(self, args: dict[str, Any], context: ToolContext) -> Any:
        from prismclaw.engine.toolkit.registry.tool_runtime import GoalBlockIntent

        call_id = args.get("_call_id", "")
        code = args.get("reason_code")
        message = args.get("reason_message")
        code = code.strip() if isinstance(code, str) else ""
        message = message.strip() if isinstance(message, str) else ""
        if not code or not message:
            return error_result(
                call_id,
                "declare_blocked：'reason_code' 与 'reason_message' 均为必填。",
            )

        context.goal_block_intent = GoalBlockIntent(code=code, message=message)
        return text_result(
            call_id,
            f"declare_blocked：已记录阻塞意图（{code}）。停止调用工具，说明"
            "阻塞条件与已交付内容；运行循环将决定是否采纳该申报。",
            metadata={"reason_code": code, "reason_message": message},
        )


# ─── AskUserQuestionTool ──────────────────────────────────────────────

class AskUserQuestionTool:
    name = "ask_user_question"
    description = "向用户提问并暂停执行，收到作答后继续。仅当需要的信息只有用户知道（偏好、意图、决策或需要用户补充的材料）且不同选择的后果差异显著时使用。文件位置、现有实现、当前状态等可发现的事实必须先探索，禁止拿来问用户；能合理假设的直接假设并在结果中声明。需要用户补充信息或上传文件时也用本工具，不要只在正文里说明。一次调用聚合本轮全部问题（通常不超过 3 个）；每题给出 2-4 个互斥选项，推荐选项放首位并在 label 后加「（推荐）」。用户答案中标记「（跳过）」或「（用户跳过了所有问题）」的题目按该题首位选项继续，并在最终结果中声明该假设，不要重复追问。"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "description": "本轮需要用户回答的全部问题，一次聚合提交，通常不超过 3 个",
                "minItems": 1,
                "maxItems": 4,
                "items": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string", "description": "问题正文，一句话，只问一个决策点"},
                        "header": {"type": "string", "description": "该问题的短标题（12 字以内），用于答案回传时对应"},
                        "options": {
                            "type": "array",
                            "description": "选项之间互斥；推荐选项放首位并在 label 后加「（推荐）」；不要放凑数项",
                            "minItems": 2,
                            "maxItems": 4,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "label": {"type": "string"},
                                    "description": {"type": "string"}
                                },
                                "required": ["label"],
                                "additionalProperties": False,
                            }
                        },
                        "multi_select": {"type": "boolean", "description": "是否允许多选", "default": False},
                    },
                    "required": ["question", "header", "options", "multi_select"],
                    "additionalProperties": False,
                }
            }
        },
        "required": ["questions"],
        "additionalProperties": False,
    }
    side_effect = False
    is_concurrency_safe = False  # interactive: pauses execution for user input
    tool_class = "read"
    effect_class = "pure_read"
    requires_interactive_user = True

    async def execute(self, args: dict[str, Any], context: ToolContext) -> Any:
        """防御性兜底：正常路径下本方法永不应被调用。

        run_loop 的 HITL 分支（check_hitl）在 dispatch 之前拦截
        AskUserQuestion 并暂停等待用户作答；执行到达这里即说明拦截链
        失效（装配缺失 / headless 误配置）。返回显式 error 而非假成功
        「已提问」——假成功会让模型以为用户已收到提问而带着幻觉继续
        执行（谎报比不提供更糟）；显式 error 让模型立刻改走可用的
        非交互路径。
        """
        call_id = args.get("_call_id", "")
        questions = args.get("questions", [])

        if not questions:
            return error_result(call_id, "ask_user_question：缺少 'questions' 参数")
        return error_result(
            call_id,
            "ask_user_question 进入了直接执行路径：HITL 拦截链未能暂停以获取用户输入"
            "（属于配置错误）。用户实际上并未被提问——请不要假设已获得回答；"
            "请改用可用的非交互方案推进，或明确说明你需要什么。",
        )
