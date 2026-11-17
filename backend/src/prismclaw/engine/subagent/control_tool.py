from __future__ import annotations

import copy
import logging
from typing import Any

from prismclaw.engine.execution.capabilities.catalog import CapabilityCatalogService
from prismclaw.engine.security.gate.types import ParallelTaskExtras
from prismclaw.engine.subagent.core.executor import SubAgentExecutor
from prismclaw.engine.subagent.runtime.collaboration import (
    SubagentCollaborationError,
    SubagentCollaborationService,
    SubagentRunLaunch,
)
from prismclaw.engine.subagent.runtime.records import AgentSessionRecord
from prismclaw.engine.toolkit.registry.tool_runtime import (
    ToolContext,
    ToolResult,
    error_result,
    text_result,
)

logger = logging.getLogger(__name__)


class SubagentControlTool:
    name = "subagent_control"
    description = (
        "管理当前主会话拥有的子代理。send 向 persistent Agent 追加消息；"
        "status/list 查询持久状态；interrupt 在安全边界中断；"
        "resume 从中断或恢复阻断状态创建新 attempt；close 永久关闭。"
        "confirm_recovery=true 仅表示接受未决工具副作用的不确定性，不表示副作用已回滚。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["send", "status", "list", "interrupt", "resume", "close"],
            },
            "agent_id": {
                "type": "string",
                "description": "sub_agent 返回的完整 ag: 标识；list 不需要。",
            },
            "content": {
                "type": "string",
                "description": "action='send' 时追加给 persistent Agent 的消息。",
            },
            "request_id": {
                "type": "string",
                "description": "可选幂等标识；send 重试时必须保持不变。",
            },
            "lifecycle": {
                "type": "string",
                "enum": ["one_shot", "persistent"],
                "description": "action='list' 时按生命周期过滤。",
            },
            "status": {
                "type": "string",
                "enum": ["idle", "running", "interrupted", "recovery_blocked", "closed"],
                "description": "action='list' 时按 Agent 状态过滤。",
            },
            "confirm_recovery": {
                "type": "boolean",
                "description": "resume 时接受未决工具副作用的不确定性；不会重放原调用。",
                "default": False,
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    }
    side_effect = True
    is_concurrency_safe = False
    tool_class = "write"
    effect_class = "local_mutation"
    read_only_allowed = True
    skill_gated = False
    visibility_scope = "main_only"

    def __init__(
        self,
        *,
        collaboration: SubagentCollaborationService,
        capability_catalog: CapabilityCatalogService,
        executor: SubAgentExecutor,
    ) -> None:
        self._collaboration = collaboration
        self._capability_catalog = capability_catalog
        self._executor = executor

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        call_id = str(args.get("_call_id") or "")
        action = args.get("action")
        if action not in {"send", "status", "list", "interrupt", "resume", "close"}:
            return error_result(call_id, f"subagent_control：无效 action {action!r}")
        agent_id = str(args.get("agent_id") or "").strip()
        if action != "list" and not agent_id:
            return error_result(call_id, "subagent_control：缺少必填参数 'agent_id'")

        try:
            if action == "list":
                agents = await self._collaboration.list(
                    session_id=context.session_id,
                    lifecycle=args.get("lifecycle"),
                    status=args.get("status"),
                )
                payload = [agent.model_dump(mode="json") for agent in agents]
                return text_result(
                    call_id,
                    f"当前会话共有 {len(agents)} 个符合条件的子代理。",
                    metadata={"agents": payload},
                )
            if action == "status":
                payload = await self._collaboration.status(
                    session_id=context.session_id,
                    agent_id=agent_id,
                )
                agent = payload["agent"]
                run = payload.get("run") or {}
                return text_result(
                    call_id,
                    (
                        f"Agent {agent_id}：status={agent['status']}，"
                        f"run_id={run.get('run_id') or '无'}，"
                        f"unacked={payload['unacked_message_count']}"
                    ),
                    metadata=payload,
                )

            agent = await self._owned_agent(context.session_id, agent_id)
            request_id = args.get("request_id")
            if action == "interrupt":
                updated = await self._collaboration.interrupt(
                    session_id=context.session_id,
                    agent_id=agent_id,
                    request_id=request_id,
                )
                return self._agent_result(call_id, "已请求在安全边界中断", updated)
            if action == "close":
                updated = await self._collaboration.close(
                    session_id=context.session_id,
                    agent_id=agent_id,
                    request_id=request_id,
                )
                return self._agent_result(call_id, "已关闭子代理", updated)

            launch = self._build_launch(
                agent,
                context,
                call_id=call_id,
            )
            if action == "send":
                content = args.get("content")
                if not isinstance(content, str) or not content.strip():
                    return error_result(
                        call_id,
                        "subagent_control：action='send' 需要非空 'content'",
                    )
                updated, message_id = await self._collaboration.send(
                    session_id=context.session_id,
                    agent_id=agent_id,
                    content=content,
                    request_id=request_id,
                    launch=launch,
                )
                result = self._agent_result(call_id, "消息已持久化并调度", updated)
                result.metadata = {**(result.metadata or {}), "message_id": message_id}
                return result

            updated = await self._collaboration.resume(
                session_id=context.session_id,
                agent_id=agent_id,
                launch=launch,
                confirm_recovery=args.get("confirm_recovery") is True,
            )
            return self._agent_result(call_id, "恢复流程已处理", updated)
        except (SubagentCollaborationError, ValueError) as exc:
            return error_result(call_id, f"subagent_control：{exc}")
        except Exception as exc:
            logger.exception("subagent control failed action=%s agent_id=%s", action, agent_id)
            return error_result(
                call_id,
                f"subagent_control：{type(exc).__name__}: {exc}",
            )

    async def _owned_agent(self, session_id: str, agent_id: str) -> AgentSessionRecord:
        agent = await self._collaboration.records.get_session(
            session_id=session_id,
            agent_id=agent_id,
        )
        if agent is None:
            raise SubagentCollaborationError(f"当前会话中未找到子代理 {agent_id!r}")
        return agent

    def _build_launch(
        self,
        agent: AgentSessionRecord,
        context: ToolContext,
        *,
        call_id: str,
    ) -> SubagentRunLaunch:
        entry = self._capability_catalog.get_agent_entry(agent.agent_name)
        if entry is None:
            raise SubagentCollaborationError(
                "definition_changed: current agent definition is unavailable"
            )
        overrides = agent.definition_snapshot.get("overrides") or {}
        if not isinstance(overrides, dict):
            raise SubagentCollaborationError("definition snapshot overrides are invalid")
        manifest = entry.manifest.model_copy(update=overrides)
        registry = self._capability_catalog.tool_registry
        if registry is None:
            raise SubagentCollaborationError("当前能力目录没有工具注册表")
        return SubagentRunLaunch(
            manifest=manifest,
            executor=self._executor,
            parent_ctx=_control_child_context(context),
            tool_registry=registry,
            call_id=call_id,
            agent_source=agent.agent_source,
            overrides=overrides,
        )

    @staticmethod
    def _agent_result(
        call_id: str,
        message: str,
        agent: AgentSessionRecord,
    ) -> ToolResult:
        result = text_result(
            call_id,
            f"{message}：{agent.agent_id}（status={agent.status}）",
            metadata={"agent": agent.model_dump(mode="json")},
        )
        result.is_error = agent.status == "recovery_blocked"
        return result


def _control_child_context(context: ToolContext) -> ToolContext:
    parent_extras = context.task_runtime
    context.task_runtime = ParallelTaskExtras(task_runtime=parent_extras.task_runtime)
    try:
        child = copy.deepcopy(context)
    finally:
        context.task_runtime = parent_extras
    child.task_runtime = ParallelTaskExtras(
        task_runtime=parent_extras.task_runtime,
        parallel_task_store=parent_extras.parallel_task_store,
    )
    child.hook_dispatcher = context.hook_dispatcher
    child.abort = context.abort
    child.task_manager = context.task_manager
    child.subagent_terminal_sink = context.subagent_terminal_sink
    return child


__all__ = ["SubagentControlTool"]
