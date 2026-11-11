"""Sub-agent executor entrypoint.

`SubAgentExecutor` remains the production-facing facade used by the
`sub_agent` tool and runtime wiring, but execution semantics live in
`agent_instance.py`. This module should stay a thin delegator only.
"""

from __future__ import annotations

import logging
from typing import Any

from prismclaw.engine.subagent.core.instance import (
    AgentInstance,
    AgentRunResult,
)
from prismclaw.engine.subagent.runtime.parallel import (
    PARALLEL_TASK_TERMINAL_STATUSES,
    ParallelTaskRuntimeRecord,
    build_parallel_task_terminal_record,
    new_parallel_task_id,
)
from prismclaw.engine.subagent.definition.manifest import AgentManifest
from prismclaw.engine.toolkit.registry.registry import ToolRegistry
from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext
from prismclaw.schema.config.config import SubAgentConfig
from prismclaw.schema.content.content_types import SubagentEnvelopeArtifact
from prismclaw.schema.content.subagent import SubagentContextSnapshot


_AUDIT_LOGGER_NAME = "prismclaw.audit.subagent"


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
        llm_client: Any,
        memory_store: Any = None,
        session_store: Any = None,
        audit_logger: logging.Logger | None = None,
        default_model: str | None = None,
        decision_audit_logger: Any = None,
        channel: str = "web",
        skill_runtime: Any = None,
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
        self._default_model = default_model or getattr(
            llm_client, "default_model", None
        )
        self._audit = audit_logger or logging.getLogger(_AUDIT_LOGGER_NAME)
        self._decision_audit = decision_audit_logger
        self._channel = channel
        self._skill_runtime = skill_runtime
        self._tool_timeout_s = tool_timeout_s

    async def run(
        self,
        manifest: AgentManifest,
        task: str,
        parent_ctx: ToolContext,
        *,
        call_id: str = "",
        context_snapshot: SubagentContextSnapshot | dict[str, Any] | None = None,
    ) -> AgentRunResult:
        """Execute a sub-agent task.

        Delegates to :class:`AgentInstance`.  The ``execution_mode`` field on
        the manifest (``sync`` / ``parallel``) controls the caller-side fan-out
        behaviour in ``SubAgentTool``; by the time we reach this method the
        execution is always synchronous (the caller awaits each run).
        """
        import uuid

        from prismclaw.engine.security.gate.types import TaskRuntimeContext

        run_id = f"run:{uuid.uuid4().hex[:12]}"
        # getattr 兜底：上游若错换类型为 TaskRuntimeState 时返回 None
        parallel_task_store = getattr(parent_ctx.task_runtime, "parallel_task_store", None)

        # Resolve runtime persistence id from the strong-typed task context.
        current_task_ctx = parent_ctx.task_runtime_ctx
        task_id = current_task_ctx.parallel_task_id

        if task_id is None and parallel_task_store is not None:
            task_id = new_parallel_task_id()
            # Update the strong-typed task runtime context.
            parent_ctx.task_runtime.task_runtime = TaskRuntimeContext(
                parallel_task_id=task_id,
                managed_task_id=current_task_ctx.managed_task_id,
                is_fork=current_task_ctx.is_fork,
                handoff_chain_id=current_task_ctx.handoff_chain_id,
                handoff_hop_index=current_task_ctx.handoff_hop_index,
                effective_hop_index=current_task_ctx.effective_hop_index,
                progress_batch_id=current_task_ctx.progress_batch_id,
                source_skill_origin=current_task_ctx.source_skill_origin,
                source_preload_skills=current_task_ctx.source_preload_skills,
                merged_from_chains=current_task_ctx.merged_from_chains,
            )

        managed_task_id = current_task_ctx.managed_task_id

        is_fork = current_task_ctx.is_fork
        kind: str = "fork" if is_fork else "subagent"
        notification_state = "suppressed"

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
            decision_audit_logger=self._decision_audit,
            channel=self._channel,
            skill_runtime=self._skill_runtime,
            is_fork=is_fork,
            tool_timeout_s=self._tool_timeout_s,
        )

        # Runtime persistence: scheduled record so the follow-up pipeline
        # knows about this task before the run completes.
        if parallel_task_store is not None and task_id is not None:
            await parallel_task_store.append(
                ParallelTaskRuntimeRecord(
                    task_id=task_id,
                    owner_session_id=parent_ctx.session_id,
                    source_call_id=call_id or None,
                    kind=kind,
                    status="scheduled",
                    run_id=run_id,
                    notification_state=notification_state,
                    managed_task_id=managed_task_id,
                    agent_name=manifest.name,
                    agent_source=getattr(manifest, "source", "user"),
                )
            )

            async def _persist_terminal_state(result: AgentRunResult) -> None:
                current = await parallel_task_store.get(
                    session_id=parent_ctx.session_id,
                    task_id=task_id,
                )
                if current is None:
                    return
                if current.status in PARALLEL_TASK_TERMINAL_STATUSES:
                    return
                outcome = result.outcome
                if outcome == "success":
                    terminal_status = "completed"
                elif outcome == "aborted":
                    terminal_status = "cancelled"
                else:
                    terminal_status = "failed"
                projection_payload = {
                    "execution_mode": manifest.execution_mode,
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
                await parallel_task_store.append(
                    build_parallel_task_terminal_record(
                        current,
                        status=terminal_status,
                        result_summary=_result_text(result) or None,
                        error_summary=None if terminal_status == "completed" else outcome,
                        projection_payload=projection_payload,
                    )
                )

            instance.on_complete(_persist_terminal_state)

        result: AgentRunResult = await instance.run(
            task,
            call_id=call_id,
            context_snapshot=context_snapshot,
        )

        # Post-run: stamp envelope with the runtime task id and flip the
        # runtime record from "scheduled" → "running" if it still is.
        if task_id is not None:
            envelope = result.tool_result.subagent_envelope
            if envelope is None:
                envelope = SubagentEnvelopeArtifact(task_id=task_id)
            else:
                envelope = envelope.model_copy(update={"task_id": task_id})
            result.tool_result.subagent_envelope = envelope
            if parallel_task_store is not None:
                # Use the managed_task_id we already resolved (possibly updated
                # via the strong-typed context).
                latest_managed_task_id = (
                    current_task_ctx.managed_task_id or managed_task_id
                )
                current = await parallel_task_store.get(
                    session_id=parent_ctx.session_id,
                    task_id=task_id,
                )
                if current is not None and current.status == "scheduled":
                    await parallel_task_store.append(
                        current.model_copy(
                            update={
                                "status": "running",
                                "started_at_epoch_ms": current.started_at_epoch_ms
                                or current.created_at_epoch_ms,
                                "managed_task_id": latest_managed_task_id,
                            },
                            deep=True,
                        )
                    )
        return result


__all__ = ["SubAgentExecutor", "AgentRunResult"]
