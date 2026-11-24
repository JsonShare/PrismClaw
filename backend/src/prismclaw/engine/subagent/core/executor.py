"""Sub-agent executor entrypoint.

`SubAgentExecutor` remains the production-facing facade used by the
`sub_agent` tool and runtime wiring, but execution semantics live in
`agent_instance.py`. This module should stay a thin delegator only.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from dataclasses import replace
from typing import Any, Literal

from prismclaw.engine.execution.skills.runtime import SkillRuntime
from prismclaw.engine.subagent.core._result_types import AgentRunResult
from prismclaw.engine.subagent.core.instance import AgentInstance
from prismclaw.engine.subagent.core.run_context import SubagentRunContext
from prismclaw.engine.subagent.definition.manifest import AgentManifest
from prismclaw.engine.subagent.runtime.parallel import (
    PARALLEL_TASK_TERMINAL_STATUSES,
    ParallelTaskRuntimeRecord,
    ParallelTaskStore,
    build_parallel_task_terminal_record,
    new_parallel_task_id,
)
from prismclaw.engine.subagent.runtime.records import new_agent_id
from prismclaw.engine.toolkit.registry.registry import ToolRegistry
from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext
from prismclaw.schema.config.config import SubAgentConfig
from prismclaw.schema.content.content_types import SubagentEnvelopeArtifact
from prismclaw.schema.content.subagent import SubagentContextSnapshot
from prismclaw.schema.llm.types import LLMClient
from prismclaw.schema.storage.storage_types import MemoryStore, SessionStore

_AUDIT_LOGGER_NAME = "prismclaw.audit.subagent"
logger = logging.getLogger(__name__)

_TerminalStatus = Literal["completed", "failed", "cancelled"]


def _result_text(result: AgentRunResult) -> str:
    blocks = getattr(result.tool_result, "content", None) or []
    for block in blocks:
        text = getattr(block, "text", None)
        if isinstance(text, str) and text:
            return text
    return ""


class SubAgentExecutor:
    def __init__(
        self,
        *,
        tool_registry: ToolRegistry,
        settings: SubAgentConfig,
        llm_client: LLMClient,
        memory_store: MemoryStore | None = None,
        session_store: SessionStore | None = None,
        audit_logger: logging.Logger | None = None,
        default_model: str | None = None,
        channel: str = "web",
        skill_runtime: SkillRuntime | None = None,
        # 子代理内部工具调用的单工具时限，应继承 agent 配置的
        # timeouts.tool_seconds（默认 120.0 与 execute_preflighted_tool_calls 的
        # 签名默认一致，保证未透传时行为不变）。
        tool_timeout_s: float = 120.0,
    ) -> None:
        self._tools = tool_registry
        self._settings = settings
        self._llm = llm_client
        self._memory_store = memory_store
        self._session_store = session_store
        self._default_model = default_model or getattr(llm_client, "default_model", None)
        self._audit = audit_logger or logging.getLogger(_AUDIT_LOGGER_NAME)
        self._channel = channel
        self._skill_runtime = skill_runtime
        self._tool_timeout_s = tool_timeout_s

    # ── 后台派发（P1a） ───────────────────────────────────────────────

    async def launch_background(
        self,
        manifest: AgentManifest,
        task: str,
        parent_ctx: ToolContext,
        *,
        call_id: str = "",
        context_snapshot: SubagentContextSnapshot | dict[str, Any] | None = None,
    ) -> tuple[str | None, str | None, str | None]:
        """派发后台子代理，立即返回 ``(task_id, agent_id, error)``。

        ``agent_id`` 在派发时就确定并随回执返回，与 ``task_id`` 并列为可寻址
        的唯一键；它随 :meth:`run` 注入子代理信封，供后续 ``subagent_control``
        使用。

        闸门与登记点都在这里：记录仍由 :meth:`run` 单点写入（scheduled →
        running → 终态），本方法只负责排队。任务以 ``TaskManager.spawn``
        登记，使优雅停机可以排水并避免进程退出时被强杀。
        """
        store = parent_ctx.task_runtime.parallel_task_store
        if store is None:
            return None, None, "后台派发需要并行任务存储，当前会话不可用。"
        task_manager = getattr(parent_ctx, "task_manager", None)
        if task_manager is None:
            return None, None, "后台派发需要任务管理器，当前运行环境不可用。"

        running = await store.list_for_session(
            session_id=parent_ctx.session_id,
            include_terminal=False,
        )
        limit = self._settings.max_parallel_per_turn
        if len(running) >= limit:
            return (
                None,
                None,
                f"本会话正在执行的子代理任务已达上限（{limit}）。请等待已有任务完成后再派发。",
            )

        task_id = new_parallel_task_id()
        agent_id = new_agent_id()
        coro = self._background_main(
            manifest,
            task,
            parent_ctx,
            task_id=task_id,
            agent_id=agent_id,
            call_id=call_id,
            context_snapshot=context_snapshot,
        )
        try:
            task_manager.spawn(
                f"subagent:{task_id}",
                coro,
                category="subagent",
                owner=parent_ctx.session_id,
            )
        except Exception:
            coro.close()
            raise
        return task_id, agent_id, None

    async def _background_main(
        self,
        manifest: AgentManifest,
        task: str,
        parent_ctx: ToolContext,
        *,
        task_id: str,
        agent_id: str,
        call_id: str,
        context_snapshot: SubagentContextSnapshot | dict[str, Any] | None,
    ) -> None:
        # 子代理 loop 对 abort 是协作式检查；若正卡在长工具调用里，
        # 仅靠检查点无法在用户中止时及时停下。这里额外监听父 run 的 abort：
        # 一旦置位就真正取消内层 run，由 run() 的统一终态出口写 cancelled。
        run_task = asyncio.create_task(
            self.run(
                manifest,
                task,
                parent_ctx,
                call_id=call_id,
                context_snapshot=context_snapshot,
                background=True,
                task_id=task_id,
                agent_id=agent_id,
            )
        )
        abort = parent_ctx.abort
        watcher = asyncio.create_task(abort.wait()) if abort is not None else None
        try:
            if watcher is None:
                await run_task
            else:
                done, _ = await asyncio.wait(
                    {run_task, watcher},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if run_task in done:
                    await run_task
                else:
                    run_task.cancel()
                    await asyncio.gather(run_task, return_exceptions=True)
        finally:
            if watcher is not None:
                watcher.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await watcher
            if not run_task.done():
                run_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await run_task

    # ── 执行 ─────────────────────────────────────────────────────────

    async def run(
        self,
        manifest: AgentManifest,
        task: str,
        parent_ctx: ToolContext,
        *,
        call_id: str = "",
        context_snapshot: SubagentContextSnapshot | dict[str, Any] | None = None,
        background: bool = False,
        task_id: str | None = None,
        agent_id: str | None = None,
        run_context: SubagentRunContext | None = None,
    ) -> AgentRunResult:
        """Execute a sub-agent task.

        Delegates to :class:`AgentInstance`. Caller-side fan-out is controlled
        exclusively by the ``sub_agent`` invocation; this method always executes
        one sub-agent run.

        ``background=True`` 只影响两点：记录在任务开始时就翻到 ``running``
        （闸门依赖该状态），以及终态写库后触发一次投递。执行控制流不变。

        ``agent_id`` 是派发时确定的身份；协作运行时以 ``run_context.agent_id``
        为准（两者是同一标识），非协作的进程内后台派发则用调用方注入的值。
        """
        run_id = run_context.run_id if run_context is not None else f"run:{uuid.uuid4().hex[:12]}"
        # 每条 Runtime 记录都必须携带 agent_id（与 task_id 组成唯一键）；
        # 进程内同步派发没有外部注入身份时，就地生成一个。
        resolved_agent_id = (
            run_context.agent_id if run_context is not None else (agent_id or new_agent_id())
        )
        store = parent_ctx.task_runtime.parallel_task_store

        current_task_ctx = parent_ctx.task_runtime_ctx
        resolved_task_id = task_id or current_task_ctx.parallel_task_id
        if resolved_task_id is None and store is not None:
            resolved_task_id = new_parallel_task_id()
        if resolved_task_id is not None:
            parent_ctx.task_runtime.task_runtime = replace(
                current_task_ctx,
                parallel_task_id=resolved_task_id,
            )

        is_fork = current_task_ctx.is_fork
        kind: str = "fork" if is_fork else "subagent"

        scheduled: ParallelTaskRuntimeRecord | None = None
        if store is not None and resolved_task_id is not None:
            scheduled = ParallelTaskRuntimeRecord(
                task_id=resolved_task_id,
                agent_id=resolved_agent_id,
                owner_session_id=parent_ctx.session_id,
                source_call_id=call_id or None,
                kind=kind,
                status="scheduled",
                run_id=run_id,
                agent_name=manifest.name,
                agent_source=getattr(manifest, "source", "user"),
            )
            await store.append(scheduled)
            # ``scheduled → running`` 必须在执行开始前落库：闸门按
            # scheduled|running 统计并发，旧实现等到 run 返回才翻 running，
            # 导致后台任务执行全程都是 scheduled，且已结束的任务仍占额度。
            await store.append(
                scheduled.model_copy(
                    update={
                        "status": "running",
                        "started_at_epoch_ms": scheduled.created_at_epoch_ms,
                    },
                    deep=True,
                )
            )

        instance = AgentInstance(
            run_id=run_id,
            manifest=manifest,
            parent_ctx=parent_ctx,
            tool_registry=self._tools,
            llm_client=self._llm,
            settings=self._settings,
            memory_store=self._memory_store,
            session_store=self._session_store,
            audit_logger=self._audit,
            default_model=self._default_model,
            channel=self._channel,
            skill_runtime=self._skill_runtime,
            is_fork=is_fork,
            tool_timeout_s=self._tool_timeout_s,
            run_context=run_context,
        )

        try:
            result = await instance.run(
                task,
                call_id=call_id,
                context_snapshot=context_snapshot,
            )
        except asyncio.CancelledError:
            # 终态写库失败不能替换 CancelledError：任务仍必须被判定为取消，
            # 失败本身通过日志暴露（而不是静默）。
            try:
                await self._finalize(
                    store,
                    parent_ctx,
                    resolved_task_id,
                    agent_id=resolved_agent_id,
                    status="cancelled",
                    error_summary="cancelled",
                    background=background,
                )
            except Exception:
                logger.exception("subagent.cancel.finalize_failed task_id=%s", resolved_task_id)
            raise
        except Exception as exc:
            logger.exception("subagent.run_crashed name=%s run_id=%s", manifest.name, run_id)
            try:
                await self._finalize(
                    store,
                    parent_ctx,
                    resolved_task_id,
                    agent_id=resolved_agent_id,
                    status="failed",
                    error_summary=f"{type(exc).__name__}: {exc}",
                    background=background,
                )
            except Exception:
                logger.exception("subagent.failure.finalize_failed task_id=%s", resolved_task_id)
            raise

        # Stamp runtime identity before persisting the terminal projection.
        envelope_updates: dict[str, Any] = {}
        if resolved_task_id is not None:
            envelope_updates["task_id"] = resolved_task_id
        if resolved_agent_id is not None:
            envelope_updates["agent_id"] = resolved_agent_id
        if run_context is not None:
            envelope_updates.update(
                {
                    "run_id": run_context.run_id,
                    "child_session_id": run_context.child_session_id,
                    "lifecycle": run_context.lifecycle,
                }
            )
        if envelope_updates:
            envelope = result.tool_result.subagent_envelope
            if envelope is None:
                envelope = SubagentEnvelopeArtifact(**envelope_updates)
            else:
                envelope = envelope.model_copy(update=envelope_updates)
            result.tool_result.subagent_envelope = envelope

        outcome = result.outcome
        terminal_status: _TerminalStatus
        if outcome == "success":
            terminal_status = "completed"
        elif outcome == "aborted":
            terminal_status = "cancelled"
        else:
            terminal_status = "failed"

        projection_payload: dict[str, Any] = {
            "fork_status": "completed" if terminal_status == "completed" else "error",
            "agent_name": manifest.name,
            "agent_source": getattr(manifest, "source", "user"),
            "outcome": outcome,
        }
        envelope = result.tool_result.subagent_envelope
        if envelope is not None:
            projection_payload["subagent_envelope"] = envelope.model_dump(
                mode="python",
                exclude_none=True,
            )

        await self._finalize(
            store,
            parent_ctx,
            resolved_task_id,
            agent_id=resolved_agent_id,
            status=terminal_status,
            result_text=_result_text(result) or None,
            error_summary=None if terminal_status == "completed" else outcome,
            projection_payload=projection_payload,
            background=background,
        )
        return result

    async def _finalize(
        self,
        store: ParallelTaskStore | None,
        parent_ctx: ToolContext,
        task_id: str | None,
        *,
        agent_id: str,
        status: _TerminalStatus,
        result_text: str | None = None,
        error_summary: str | None = None,
        projection_payload: dict[str, Any] | None = None,
        background: bool,
    ) -> None:
        """把非终态记录翻成终态；后台任务随后触发一次投递。

        终态写库是唯一出口：正常完成、异常、取消三条路径都经过这里，
        避免回调机制在 CancelledError 下被跳过而留下无主记录。
        """
        record: ParallelTaskRuntimeRecord | None = None
        if store is not None and task_id is not None:
            current = await store.get(
                session_id=parent_ctx.session_id,
                agent_id=agent_id,
                task_id=task_id,
            )
            if current is not None and current.status not in PARALLEL_TASK_TERMINAL_STATUSES:
                record = build_parallel_task_terminal_record(
                    current,
                    status=status,
                    result_summary=result_text,
                    error_summary=error_summary,
                    projection_payload=projection_payload,
                )
                await store.append(record)
            else:
                record = current

        if not background or record is None:
            return
        sink = parent_ctx.subagent_terminal_sink
        if sink is None:
            return
        try:
            await sink(record)
        except Exception:
            logger.warning(
                "subagent.background.notify_failed task_id=%s",
                record.task_id,
                exc_info=True,
            )


__all__ = ["SubAgentExecutor"]
