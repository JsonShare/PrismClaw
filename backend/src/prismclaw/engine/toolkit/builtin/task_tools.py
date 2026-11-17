from __future__ import annotations

from typing import Any

from prismclaw.engine.subagent.runtime.background import cancel_background_task
from prismclaw.engine.subagent.runtime.follow_up import (
    SessionFollowUpSnapshot,
    format_parallel_task_attention,
    format_parallel_task_summary,
    session_follow_up_snapshot,
)
from prismclaw.engine.subagent.runtime.parallel import (
    PARALLEL_TASK_TERMINAL_STATUSES,
    ParallelTaskRuntimeRecord,
    latest_parallel_task_for_tree,
)
from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext, error_result, text_result

# ─── Task helper functions ────────────────────────────────────────────

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
        "projected_to_session": False,
    }
    if record.result_summary is not None:
        metadata["result_summary"] = record.result_summary
    if record.error_summary is not None:
        metadata["error_summary"] = record.error_summary
    if record.projection_payload:
        metadata["projection_payload"] = dict(record.projection_payload)
    return metadata


def _session_follow_up_snapshot_for_context(
    context: ToolContext,
) -> SessionFollowUpSnapshot:
    session_tree = context.session_tree
    if session_tree is None:
        return SessionFollowUpSnapshot()
    return session_follow_up_snapshot(session_tree)


def _parallel_task_metadata_with_projection(
    record: ParallelTaskRuntimeRecord,
    context: ToolContext,
) -> dict[str, Any]:
    metadata = _parallel_task_to_metadata(record)
    projection = _task_projection_for_context(context, task_id=record.task_id)
    if projection is not None:
        metadata["fork_task_projection"] = projection
        metadata["projected_to_session"] = True
    return metadata


def _format_parallel_task_summary(
    record: ParallelTaskRuntimeRecord,
    *,
    projection: dict[str, Any] | None = None,
) -> str:
    summary = (
        f"Task {record.task_id}: {record.agent_name or record.kind}"
        f" | category={record.kind} | state={record.status}"
    )
    if record.error_summary:
        summary += f"\nError: {record.error_summary}"
    if record.result_summary:
        summary += f"\nResult:\n{record.result_summary}"
    if projection is not None:
        summary += "\nSession projection available."
    return summary


# ─── CheckSubagentTaskTool ────────────────────────────────────────────

class CheckSubagentTaskTool:
    name = "check_subagent_task"
    description = (
        "查询子代理并行任务的状态与结果。传入 sub_agent 派发时返回的完整 task_id"
        "（不得截断）。历史状态会过期，需要时重新查询；仅在需要结果或确认进度时"
        "调用一次，不要循环轮询。任务未完成时结果字段为空，不得据此综合结论。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "子代理任务 ID，形如 pt:xxxxxxxxxxxx。",
            },
        },
        "required": ["task_id"],
        "additionalProperties": False,
    }
    side_effect = False
    is_concurrency_safe = True
    tool_class = "read"
    effect_class = "read"
    skill_gated = False  # 派发回执直接引用本工具；不可 skill_gated，否则后台任务无法查询
    visibility_scope = "main_only"

    async def execute(self, args: dict[str, Any], context: ToolContext) -> Any:
        call_id = args.get("_call_id", "")
        raw_task_id = args.get("task_id")
        task_id = raw_task_id.strip() if isinstance(raw_task_id, str) else ""
        if not task_id:
            return error_result(call_id, "check_subagent_task：缺少必需参数 'task_id'。")

        parallel_task_store = context.task_runtime.parallel_task_store
        if parallel_task_store is None:
            return error_result(
                call_id,
                "check_subagent_task：当前会话中并行任务运行时不可用。",
            )

        record = await parallel_task_store.get(
            session_id=context.session_id,
            task_id=task_id,
        )
        if record is None:
            return error_result(
                call_id,
                f"check_subagent_task：当前会话中未找到并行任务 {task_id!r}。",
            )

        projection = _task_projection_for_context(context, task_id=task_id)
        metadata = {"task": _parallel_task_metadata_with_projection(record, context)}
        return text_result(
            call_id,
            _format_parallel_task_summary(record, projection=projection),
            metadata=metadata,
        )


# ─── ListSubagentTasksTool ────────────────────────────────────────────

class ListSubagentTasksTool:
    name = "list_subagent_tasks"
    description = (
        "列出当前会话的子代理并行任务及状态，用于确认哪些任务仍在运行或已结束。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    side_effect = False
    is_concurrency_safe = True
    tool_class = "read"
    effect_class = "read"
    skill_gated = False
    visibility_scope = "main_only"

    async def execute(self, args: dict[str, Any], context: ToolContext) -> Any:
        call_id = args.get("_call_id", "")

        parallel_task_store = context.task_runtime.parallel_task_store
        if parallel_task_store is None:
            return error_result(
                call_id,
                "list_subagent_tasks：当前会话中并行任务运行时不可用。",
            )

        records = await parallel_task_store.list_for_session(
            session_id=context.session_id,
            include_terminal=True,
        )
        if not records:
            return text_result(
                call_id,
                "当前会话没有子代理任务。",
                metadata={"tasks": []},
            )

        lines = [f"共 {len(records)} 个子代理任务："]
        for record in records:
            lines.append(
                f"- {record.task_id} | {record.agent_name or record.kind}"
                f" | state={record.status}"
            )
        metadata: dict[str, Any] = {
            "tasks": [
                _parallel_task_metadata_with_projection(record, context)
                for record in records
            ],
        }
        snapshot = _session_follow_up_snapshot_for_context(context)
        text = "\n".join(lines)
        if snapshot.parallel_task_summary is not None:
            text += "\n" + format_parallel_task_summary(snapshot.parallel_task_summary)
            metadata["parallel_task_summary"] = snapshot.parallel_task_summary
        if snapshot.parallel_task_attention is not None:
            text += "\n" + format_parallel_task_attention(snapshot.parallel_task_attention)
            metadata["parallel_task_attention"] = snapshot.parallel_task_attention
        return text_result(call_id, text, metadata=metadata)


# ─── CancelTaskTool ───────────────────────────────────────────────────

class CancelTaskTool:
    name = "cancel_task"
    description = (
        "停止正在运行的后台子代理任务。请提供派发时返回的完整 task_id（不得截断）。"
        "同步执行的子代理无法中止，会在本轮结束时返回结果。"
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
    # 取消任务不修改用户文件，只控制运行时；read-only 模式下必须能停止
    # 由 read_only_allowed 的 sub_agent 派生的后台任务。
    read_only_allowed = True
    skill_gated = False  # 与 check/list 同域可见：后台任务必须可被停止
    visibility_scope = "main_only"

    async def execute(self, args: dict[str, Any], context: ToolContext) -> Any:
        call_id = args.get("_call_id", "")
        raw_task_id = args.get("task_id")
        task_id = raw_task_id.strip() if isinstance(raw_task_id, str) else ""
        if not task_id:
            return error_result(call_id, "cancel_task：缺少必需参数 'task_id'。")

        parallel_task_store = context.task_runtime.parallel_task_store
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

        if record.status in PARALLEL_TASK_TERMINAL_STATUSES:
            return error_result(
                call_id,
                f"cancel_task：并行任务 {task_id!r} 已结束（state={record.status}）。",
            )

        # 只有后台派发的任务在本进程有真实句柄；同步任务在父级工具调用栈内，
        # 不能用父级 managed_task_id 取消（那会终止整轮父会话）。
        cancelled = await cancel_background_task(task_id)
        latest = await parallel_task_store.get(
            session_id=context.session_id,
            task_id=task_id,
        )
        if not cancelled:
            if latest is not None and latest.status in PARALLEL_TASK_TERMINAL_STATUSES:
                return error_result(
                    call_id,
                    f"cancel_task：并行任务 {task_id!r} 已结束（state={latest.status}）。",
                )
            return error_result(
                call_id,
                f"cancel_task：并行任务 {task_id!r} 不是本进程可取消的后台任务"
                "（同步执行的子代理无法中止，会在本轮结束时返回结果）。",
            )

        state = latest.status if latest is not None else "cancelled"
        return text_result(
            call_id,
            f"已取消后台任务 {task_id!r}（state={state}）。",
            metadata={"task_id": task_id, "state": state},
        )


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
