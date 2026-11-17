from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from prismclaw.engine.subagent.core._result_types import AgentRunResult
from prismclaw.engine.subagent.core.contract import (
    coerce_context_snapshot,
    render_task_envelope,
)
from prismclaw.engine.subagent.core.persistence import SubagentPersistence
from prismclaw.engine.subagent.core.run_context import SubagentRunContext
from prismclaw.engine.subagent.definition.manifest import AgentManifest
from prismclaw.engine.subagent.prompting import expand_allowed_tool_names
from prismclaw.engine.subagent.runtime.lease import (
    LocalSubagentLeaseStore,
    RedisSubagentLeaseStore,
    SubagentLease,
    SubagentLeaseStore,
)
from prismclaw.engine.subagent.runtime.parallel import new_parallel_task_id
from prismclaw.engine.subagent.runtime.records import (
    AgentLifecycle,
    AgentRunRecord,
    AgentSessionRecord,
    SessionSubagentRecordStore,
    new_agent_id,
    new_run_id,
)
from prismclaw.engine.subagent.runtime.recovery import assess_and_repair_recovery
from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext
from prismclaw.schema.content.subagent import SubagentContextSnapshot
from prismclaw.schema.session.types import MessageEntry, now_iso
from prismclaw.schema.storage.storage_types import SessionStore

logger = logging.getLogger(__name__)

_ACTIVE_RUN_STATUSES = {"queued", "running"}
_STOP_BOUNDARIES = {"interrupt_requested", "close_requested"}


class SubagentCollaborationError(RuntimeError):
    pass


class SubagentConflictError(SubagentCollaborationError):
    pass


@dataclass(slots=True)
class SubagentRunLaunch:
    manifest: AgentManifest
    executor: Any
    parent_ctx: ToolContext
    tool_registry: Any
    context_snapshot: SubagentContextSnapshot | dict[str, Any] | None = None
    call_id: str = ""
    agent_source: str = "user"
    overrides: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SubagentSpawnOutcome:
    agent: AgentSessionRecord
    run: AgentRunRecord | None
    result: AgentRunResult | None = None


@dataclass(slots=True)
class _RunHandle:
    agent: AgentSessionRecord
    run: AgentRunRecord
    lease: SubagentLease
    launch: SubagentRunLaunch
    persistence: SubagentPersistence
    run_context: SubagentRunContext
    task_id: str
    background: bool
    completion: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[None] | None = None
    started: bool = False
    result: AgentRunResult | None = None
    error: BaseException | None = None


class SubagentCollaborationService:
    """Serialize per-Agent scheduling and persist every scheduling decision."""

    def __init__(
        self,
        *,
        session_store: SessionStore,
        redis_client: Any = None,
        task_manager: Any = None,
        lease_store: SubagentLeaseStore | None = None,
        worker_id: str | None = None,
    ) -> None:
        self._session_store = session_store
        self.records = SessionSubagentRecordStore(session_store)
        self._leases = lease_store or (
            RedisSubagentLeaseStore(redis_client)
            if redis_client is not None
            else LocalSubagentLeaseStore()
        )
        self._task_manager = task_manager
        self._worker_id = worker_id or f"worker:{uuid.uuid4()}"
        self._handles: dict[str, _RunHandle] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._orphan_task: asyncio.Task[None] | None = None

    @property
    def worker_id(self) -> str:
        return self._worker_id

    def set_worker_id(self, worker_id: str) -> None:
        self._worker_id = worker_id

    def start(self, *, orphan_limit: int = 20) -> None:
        if self._orphan_task is not None and not self._orphan_task.done():
            return
        self._orphan_task = asyncio.create_task(
            self.reconcile_orphans(limit=orphan_limit),
            name="subagent:orphan-reconcile",
        )

    def _lock_for(self, agent_id: str) -> asyncio.Lock:
        return self._locks.setdefault(agent_id, asyncio.Lock())

    async def spawn(
        self,
        launch: SubagentRunLaunch,
        *,
        task: str,
        lifecycle: AgentLifecycle,
        request_id: str,
        background: bool = False,
    ) -> SubagentSpawnOutcome:
        owner_session_id = launch.parent_ctx.session_id
        agent_id = _deterministic_agent_id(
            owner_session_id,
            request_id,
            launch.manifest.name,
        )
        lock = self._lock_for(agent_id)
        handle_to_wait: _RunHandle | None = None
        execute_here = False
        initial_run_id = new_run_id()
        async with lock:
            agent = await self.records.get_session(
                session_id=owner_session_id,
                agent_id=agent_id,
            )
            if agent is None:
                persistence = await SubagentPersistence.create(
                    self._session_store,
                    parent_session_id=owner_session_id,
                    agent_id=agent_id,
                    workspace_id=launch.parent_ctx.workspace_id,
                    task=task,
                    context_mode=launch.manifest.context_mode,
                    context_snapshot=launch.context_snapshot,
                    request_id=request_id,
                    run_id=initial_run_id,
                    max_inherit_messages=launch.manifest.max_inherit_messages,
                    title=f"Subagent {launch.manifest.name}",
                )
                snapshot = build_definition_snapshot(launch)
                agent = AgentSessionRecord(
                    agent_id=agent_id,
                    owner_session_id=owner_session_id,
                    child_session_id=persistence.child_session_id,
                    agent_name=launch.manifest.name,
                    agent_source=launch.agent_source,
                    lifecycle=lifecycle,
                    definition_snapshot=snapshot,
                    definition_fingerprint=_fingerprint(snapshot),
                )
                await self.records.append(agent)
            else:
                await self._validate_spawn_replay(
                    agent,
                    launch,
                    task=task,
                    lifecycle=lifecycle,
                    request_id=request_id,
                )
                persistence = await self._load_persistence(agent)

            run = await self._active_run(agent)
            if run is not None:
                handle_to_wait = self._handles.get(agent_id)
            elif agent.status == "closed" or not persistence.get_unacked_user_messages():
                run = await self._latest_run(agent)
            else:
                if agent.status in {"interrupted", "recovery_blocked"}:
                    # 未决工具副作用必须先评估；spawn 不能绕过 resume 直接重启，
                    # 否则 IN_PROGRESS 的写入调用可能被再次分发。
                    if agent.status == "recovery_blocked":
                        raise SubagentCollaborationError(
                            f"agent {agent_id!r} is recovery_blocked; "
                            "resolve it via subagent_control.resume"
                        )
                    assessment = await assess_and_repair_recovery(persistence)
                    if assessment.blocked:
                        await self._block_recovery(
                            agent,
                            await self._latest_run(agent),
                            "; ".join(assessment.reasons),
                        )
                        raise SubagentCollaborationError(
                            f"agent {agent_id!r} has unresolved tool effects; "
                            "resolve it via subagent_control.resume with confirm_recovery"
                        )
                    agent = await self._append_agent(
                        agent,
                        status="idle",
                        active_run_id=None,
                    )
                handle = await self._start_run(
                    agent,
                    launch,
                    persistence,
                    run_id=initial_run_id,
                    background=background,
                )
                handle_to_wait = handle
                if background:
                    await self._schedule(handle)
                else:
                    handle.started = True
                    execute_here = True
                run = handle.run

        if background:
            return await self._spawn_outcome(
                owner_session_id,
                agent_id,
                run.run_id if run else None,
            )
        if handle_to_wait is not None:
            if execute_here:
                await self._execute_handle(handle_to_wait)
            elif handle_to_wait.started:
                await handle_to_wait.completion.wait()
            if handle_to_wait.error is not None:
                raise handle_to_wait.error
        return await self._spawn_outcome(
            owner_session_id,
            agent_id,
            run.run_id if run else None,
            result=handle_to_wait.result if handle_to_wait else None,
        )

    async def send(
        self,
        *,
        session_id: str,
        agent_id: str,
        content: str,
        request_id: str | None = None,
        launch: SubagentRunLaunch | None = None,
    ) -> tuple[AgentSessionRecord, str]:
        async with self._lock_for(agent_id):
            agent = await self._owned_agent(session_id, agent_id)
            if agent.lifecycle != "persistent":
                raise SubagentCollaborationError("one_shot agents cannot accept follow-up messages")
            if agent.status == "closed":
                raise SubagentCollaborationError("closed agents cannot accept messages")
            persistence = await self._load_persistence(agent)
            resolved_request_id = request_id or f"send:{uuid.uuid4()}"
            message_id = _deterministic_message_id(agent_id, resolved_request_id)
            existing = _find_message(persistence, message_id=message_id)
            if existing is not None:
                if existing.content != content:
                    raise SubagentConflictError(
                        f"request_id {resolved_request_id!r} was already used "
                        "with different content"
                    )
            else:
                active = await self._active_run(agent)
                await persistence.append_user_message(
                    content,
                    message_id=message_id,
                    request_id=resolved_request_id,
                    run_id=active.run_id if active else None,
                    metadata={"kind": "follow_up"},
                    entry_id=f"submsg:{uuid.uuid5(uuid.NAMESPACE_URL, message_id).hex}",
                )
            agent = await self._reconcile_locked(agent, launch=launch)
            return agent, message_id

    async def interrupt(
        self,
        *,
        session_id: str,
        agent_id: str,
        request_id: str | None = None,
    ) -> AgentSessionRecord:
        del request_id
        return await self._request_stop(
            session_id=session_id,
            agent_id=agent_id,
            close=False,
        )

    async def close(
        self,
        *,
        session_id: str,
        agent_id: str,
        request_id: str | None = None,
    ) -> AgentSessionRecord:
        del request_id
        return await self._request_stop(
            session_id=session_id,
            agent_id=agent_id,
            close=True,
        )

    async def _request_stop(
        self,
        *,
        session_id: str,
        agent_id: str,
        close: bool,
    ) -> AgentSessionRecord:
        async with self._lock_for(agent_id):
            agent = await self._owned_agent(session_id, agent_id)
            if agent.status == "closed":
                return agent
            boundary = "close_requested" if close else "interrupt_requested"
            run = await self._active_run(agent)
            if run is not None:
                await self.records.append(
                    run.model_copy(update={"safe_boundary": boundary})
                )
            handle = self._handles.get(agent_id)
            if handle is not None:
                handle.run_context.interrupt_event.set()
            if close:
                return await self._append_agent(agent, status="closed", closed_at=now_iso())
            if run is None:
                return await self._append_agent(
                    agent,
                    status="interrupted",
                    active_run_id=None,
                )
            return agent

    async def resume(
        self,
        *,
        session_id: str,
        agent_id: str,
        launch: SubagentRunLaunch | None,
        confirm_recovery: bool = False,
    ) -> AgentSessionRecord:
        async with self._lock_for(agent_id):
            agent = await self._owned_agent(session_id, agent_id)
            if agent.lifecycle != "persistent":
                raise SubagentCollaborationError("one_shot agents cannot be resumed")
            if agent.status == "closed":
                raise SubagentCollaborationError("closed agents cannot be resumed")
            if agent.status not in {"interrupted", "recovery_blocked"}:
                return agent
            persistence = await self._load_persistence(agent)
            assessment = await assess_and_repair_recovery(
                persistence,
                confirm_recovery=confirm_recovery,
            )
            latest = await self._latest_run(agent)
            if assessment.blocked:
                return await self._block_recovery(
                    agent,
                    latest,
                    "; ".join(assessment.reasons),
                )
            if launch is None:
                return await self._block_recovery(
                    agent,
                    latest,
                    "definition_changed: current agent definition is unavailable",
                )
            current_fingerprint = _fingerprint(build_definition_snapshot(launch))
            if current_fingerprint != agent.definition_fingerprint:
                return await self._block_recovery(
                    agent,
                    latest,
                    "definition_changed: close this agent and spawn a new one",
                )
            agent = await self._append_agent(
                agent,
                status="idle",
                active_run_id=None,
            )
            return await self._reconcile_locked(
                agent,
                launch=launch,
                resumed_from_run_id=latest.run_id if latest else None,
                force_run=True,
            )

    async def status(self, *, session_id: str, agent_id: str) -> dict[str, Any]:
        agent = await self._owned_agent(session_id, agent_id)
        runs = await self.records.list_runs(session_id=session_id, agent_id=agent_id)
        active = await self._active_run(agent)
        latest = active or (runs[-1] if runs else None)
        persistence = await self._load_persistence(agent)
        return {
            "agent": agent.model_dump(mode="json"),
            "run": latest.model_dump(mode="json") if latest else None,
            "unacked_message_count": len(persistence.get_unacked_user_messages()),
        }

    async def list(
        self,
        *,
        session_id: str,
        lifecycle: AgentLifecycle | None = None,
        status: str | None = None,
    ) -> list[AgentSessionRecord]:
        statuses = {status} if status is not None else None
        return await self.records.list_sessions(
            session_id=session_id,
            lifecycle=lifecycle,
            statuses=statuses,  # type: ignore[arg-type]
        )

    async def reconcile(
        self,
        *,
        session_id: str,
        agent_id: str,
        launch: SubagentRunLaunch | None = None,
    ) -> AgentSessionRecord:
        async with self._lock_for(agent_id):
            agent = await self._owned_agent(session_id, agent_id)
            return await self._reconcile_locked(agent, launch=launch)

    async def _reconcile_locked(
        self,
        agent: AgentSessionRecord,
        *,
        launch: SubagentRunLaunch | None,
        resumed_from_run_id: str | None = None,
        force_run: bool = False,
    ) -> AgentSessionRecord:
        persistence = await self._load_persistence(agent)
        run = await self._active_run(agent)
        if run is not None:
            if agent.agent_id in self._handles:
                return agent
            lease = await self._leases.get(agent.agent_id)
            if (
                lease is not None
                and lease.run_id == run.run_id
                and await self._leases.validate(lease)
            ):
                return agent
            assessment = await assess_and_repair_recovery(persistence)
            reason = "; ".join(assessment.reasons) or "lease lost or expired"
            await self.records.append(
                run.model_copy(
                    update={
                        "status": "interrupted",
                        "safe_boundary": "recovery_assessed",
                        "recovery_reason": reason,
                        "finished_at": now_iso(),
                    }
                )
            )
            if lease is not None:
                await self._leases.release(lease)
            return await self._append_agent(
                agent,
                status="recovery_blocked" if assessment.blocked else "interrupted",
                active_run_id=None,
            )

        if agent.active_run_id is not None or agent.status == "running":
            agent = await self._append_agent(
                agent,
                status="interrupted",
                active_run_id=None,
            )
            return agent
        if agent.status in {"closed", "interrupted", "recovery_blocked"}:
            return agent
        if launch is None or (not force_run and not persistence.get_unacked_user_messages()):
            return agent
        if _fingerprint(build_definition_snapshot(launch)) != agent.definition_fingerprint:
            return await self._block_recovery(
                agent,
                await self._latest_run(agent),
                "definition_changed: close this agent and spawn a new one",
            )
        handle = await self._start_run(
            agent,
            launch,
            persistence,
            resumed_from_run_id=resumed_from_run_id,
            background=True,
        )
        await self._schedule(handle)
        return handle.agent

    async def _start_run(
        self,
        agent: AgentSessionRecord,
        launch: SubagentRunLaunch,
        persistence: SubagentPersistence,
        *,
        run_id: str | None = None,
        resumed_from_run_id: str | None = None,
        background: bool,
    ) -> _RunHandle:
        run_id = run_id or new_run_id()
        token = uuid.uuid4().hex
        lease = await self._leases.claim(
            agent.agent_id,
            run_id,
            self._worker_id,
            token,
            owner_session_id=agent.owner_session_id,
        )
        if lease is None:
            existing = await self._leases.get(agent.agent_id)
            if existing is not None and not await self._leases.validate(existing):
                lease = await self._leases.takeover(
                    existing,
                    run_id,
                    token,
                    new_owner_worker_id=self._worker_id,
                    owner_session_id=agent.owner_session_id,
                )
        if lease is None:
            raise SubagentCollaborationError(
                f"agent {agent.agent_id!r} already has an active lease"
            )

        # Local lease store 的 fence 计数随进程重启归零，而持久记录可能已到更高
        # fence。记录侧必须保持单调，否则 (fence, seq) 折叠会重新选中重启前的
        # 旧状态，导致 status/send/resume 读到过期 agent。
        fence = max(lease.fence, agent.current_fence + 1)
        runs = await self.records.list_runs(
            session_id=agent.owner_session_id,
            agent_id=agent.agent_id,
        )
        previous = next(
            (item for item in reversed(runs) if item.run_id == resumed_from_run_id),
            None,
        )
        run = AgentRunRecord(
            run_id=run_id,
            agent_id=agent.agent_id,
            owner_session_id=agent.owner_session_id,
            child_session_id=agent.child_session_id,
            task_id=new_parallel_task_id(),
            attempt=previous.attempt + 1 if previous else 1,
            resumed_from_run_id=resumed_from_run_id,
            fence=fence,
            owner_worker_id=self._worker_id,
            status="running",
            safe_boundary="run_started",
            started_at=now_iso(),
        )
        agent = agent.model_copy(
            update={
                "status": "running",
                "active_run_id": run_id,
                "current_fence": fence,
                "updated_at": now_iso(),
            }
        )
        try:
            await self.records.append(run)
            await self.records.append(agent)
        except Exception:
            await self._leases.release(lease)
            raise
        run_context = SubagentRunContext(
            agent_id=agent.agent_id,
            run_id=run_id,
            persistence=persistence,
            lifecycle=agent.lifecycle,
            lease=lease,
            lease_store=self._leases,
            attempt=run.attempt,
            definition_fingerprint=agent.definition_fingerprint,
            control_probe=lambda: self._control_requested(
                agent.owner_session_id,
                run_id,
            ),
        )
        handle = _RunHandle(
            agent=agent,
            run=run,
            lease=lease,
            launch=launch,
            persistence=persistence,
            run_context=run_context,
            task_id=run.task_id or new_parallel_task_id(),
            background=background,
        )
        self._handles[agent.agent_id] = handle
        return handle

    async def _schedule(self, handle: _RunHandle) -> None:
        if handle.started:
            return
        handle.started = True
        coro = self._background_entry(handle)
        task_manager = handle.launch.parent_ctx.task_manager or self._task_manager
        if task_manager is not None:
            try:
                task_manager.spawn(
                    f"subagent:{handle.run.run_id}",
                    coro,
                    category="subagent",
                    owner=handle.agent.owner_session_id,
                )
            except Exception as exc:
                coro.close()
                handle.started = False
                handle.error = exc
                try:
                    await self._finalize_handle(handle, outcome="error", error=exc)
                finally:
                    handle.completion.set()
                raise
            return
        handle.task = asyncio.create_task(
            coro,
            name=f"subagent:{handle.run.run_id}",
        )

    async def _background_entry(self, handle: _RunHandle) -> None:
        try:
            await self._execute_handle(handle)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("subagent collaboration run failed run_id=%s", handle.run.run_id)

    async def _execute_handle(self, handle: _RunHandle) -> AgentRunResult | None:
        if not handle.started:
            handle.started = True
        try:
            result = await handle.launch.executor.run(
                handle.launch.manifest,
                _latest_task_text(handle.persistence),
                handle.launch.parent_ctx,
                call_id=handle.launch.call_id,
                context_snapshot=handle.launch.context_snapshot,
                background=handle.background,
                task_id=handle.task_id,
                run_context=handle.run_context,
            )
        except asyncio.CancelledError as exc:
            handle.error = exc
            with contextlib.suppress(Exception):
                await self._finalize_handle(handle, outcome="cancelled", error=exc)
            raise
        except Exception as exc:
            handle.error = exc
            await self._finalize_handle(handle, outcome="error", error=exc)
            raise
        else:
            handle.result = result
            await self._finalize_handle(handle, result=result)
            return result
        finally:
            if not handle.completion.is_set():
                with contextlib.suppress(Exception):
                    await self._leases.release(handle.lease)
                # finalize 可能已通过 reconcile 启动了续跑 run 并写入新 handle，
                # 只能移除仍指向本次 handle 的登记，否则会误删继任者。
                if self._handles.get(handle.agent.agent_id) is handle:
                    self._handles.pop(handle.agent.agent_id, None)
            handle.completion.set()

    async def _finalize_handle(
        self,
        handle: _RunHandle,
        *,
        result: AgentRunResult | None = None,
        outcome: str | None = None,
        error: BaseException | None = None,
    ) -> None:
        agent = await self.records.get_session(
            session_id=handle.agent.owner_session_id,
            agent_id=handle.agent.agent_id,
        ) or handle.agent
        run = await self.records.get_run(
            session_id=handle.agent.owner_session_id,
            run_id=handle.run.run_id,
        ) or handle.run
        lease_valid = await self._leases.validate(handle.lease)
        requested_boundary = run.safe_boundary
        resolved_outcome = outcome or (result.outcome if result else "error")
        recovery_reason = handle.run_context.recovery_blocked_reason
        if not lease_valid or handle.run_context.lease_lost:
            run_status = "interrupted"
            agent_status = "interrupted"
        elif agent.status == "closed" or requested_boundary == "close_requested":
            run_status = "cancelled"
            agent_status = "closed"
        elif recovery_reason:
            run_status = "failed"
            agent_status = "recovery_blocked"
        elif (
            resolved_outcome in {"aborted", "cancelled"}
            or requested_boundary == "interrupt_requested"
        ):
            run_status = (
                "interrupted"
                if requested_boundary == "interrupt_requested"
                else "cancelled"
            )
            agent_status = "closed" if agent.lifecycle == "one_shot" else "interrupted"
        elif resolved_outcome == "success":
            run_status = "succeeded"
            agent_status = "closed" if agent.lifecycle == "one_shot" else "idle"
        else:
            run_status = "failed"
            agent_status = "closed" if agent.lifecycle == "one_shot" else "idle"

        envelope = result.tool_result.subagent_envelope if result else None
        error_summary = (
            f"{type(error).__name__}: {error}"
            if error is not None
            else recovery_reason or (None if run_status == "succeeded" else resolved_outcome)
        )
        await self.records.append(
            run.model_copy(
                update={
                    "status": run_status,
                    "safe_boundary": "finalized",
                    "recovery_reason": recovery_reason,
                    "result_envelope": (
                        envelope.model_dump(mode="json", exclude_none=True)
                        if envelope
                        else None
                    ),
                    "error_summary": error_summary,
                    "finished_at": now_iso(),
                }
            )
        )
        agent = await self._append_agent(
            agent,
            status=agent_status,
            active_run_id=None,
            closed_at=now_iso() if agent_status == "closed" else None,
        )
        await self._leases.release(handle.lease)
        if self._handles.get(agent.agent_id) is handle:
            self._handles.pop(agent.agent_id, None)

        should_reconcile = (
            agent.lifecycle == "persistent"
            and agent.status == "idle"
            and (
                run_status == "succeeded"
                or await self._has_pending_newer_than(agent, run.started_at)
            )
        )
        if should_reconcile:
            await self.reconcile(
                session_id=agent.owner_session_id,
                agent_id=agent.agent_id,
                launch=handle.launch,
            )

    async def reconcile_orphans(self, *, limit: int = 20) -> None:
        active = await self._leases.list_active()
        for item in active[: max(0, limit)]:
            try:
                agent = await self.records.get_session(
                    session_id=item.owner_session_id,
                    agent_id=item.agent_id,
                )
                if agent is not None:
                    await self.reconcile(
                        session_id=item.owner_session_id,
                        agent_id=item.agent_id,
                    )
            except Exception:
                logger.exception("subagent orphan reconcile failed agent_id=%s", item.agent_id)

    async def _control_requested(self, session_id: str, run_id: str) -> bool:
        run = await self.records.get_run(session_id=session_id, run_id=run_id)
        if run is None:
            return True
        if run.safe_boundary in _STOP_BOUNDARIES:
            return True
        agent = await self.records.get_session(session_id=session_id, agent_id=run.agent_id)
        return agent is None or agent.status == "closed"

    async def _validate_spawn_replay(
        self,
        agent: AgentSessionRecord,
        launch: SubagentRunLaunch,
        *,
        task: str,
        lifecycle: AgentLifecycle,
        request_id: str,
    ) -> None:
        if agent.lifecycle != lifecycle:
            raise SubagentConflictError("request_id was already used with another lifecycle")
        previous_manifest = agent.definition_snapshot.get("manifest")
        current_manifest = build_definition_snapshot(launch)["manifest"]
        if previous_manifest != current_manifest:
            raise SubagentConflictError("request_id was already used with another agent definition")
        persistence = await self._load_persistence(agent)
        snapshot = (
            SubagentContextSnapshot(task_goal=task)
            if launch.context_snapshot in (None, {})
            else coerce_context_snapshot(launch.context_snapshot, fallback_task=task)
        )
        expected = render_task_envelope(task, snapshot)
        for entry in persistence.tree.get_branch():
            if not isinstance(entry, MessageEntry) or entry.role != "user":
                continue
            metadata = entry.metadata or {}
            if metadata.get("kind") == "task" and metadata.get("request_id") == request_id:
                if entry.content != expected:
                    raise SubagentConflictError("request_id was already used with another task")
                return
        raise SubagentConflictError("spawn request has no matching initial task")

    async def _owned_agent(self, session_id: str, agent_id: str) -> AgentSessionRecord:
        agent = await self.records.get_session(session_id=session_id, agent_id=agent_id)
        if agent is None:
            raise SubagentCollaborationError(f"subagent {agent_id!r} was not found")
        return agent

    async def _load_persistence(self, agent: AgentSessionRecord) -> SubagentPersistence:
        persistence = await SubagentPersistence.load(
            self._session_store,
            agent.child_session_id,
        )
        if persistence is None:
            raise SubagentCollaborationError(
                f"child session for agent {agent.agent_id!r} was not found"
            )
        return persistence

    async def _active_run(self, agent: AgentSessionRecord) -> AgentRunRecord | None:
        if agent.active_run_id is None:
            return None
        run = await self.records.get_run(
            session_id=agent.owner_session_id,
            run_id=agent.active_run_id,
        )
        return run if run is not None and run.status in _ACTIVE_RUN_STATUSES else None

    async def _latest_run(self, agent: AgentSessionRecord) -> AgentRunRecord | None:
        runs = await self.records.list_runs(
            session_id=agent.owner_session_id,
            agent_id=agent.agent_id,
        )
        return runs[-1] if runs else None

    async def _append_agent(
        self,
        agent: AgentSessionRecord,
        **updates: Any,
    ) -> AgentSessionRecord:
        updates["updated_at"] = now_iso()
        updated = agent.model_copy(update=updates)
        await self.records.append(updated)
        return updated

    async def _block_recovery(
        self,
        agent: AgentSessionRecord,
        run: AgentRunRecord | None,
        reason: str,
    ) -> AgentSessionRecord:
        if run is not None:
            await self.records.append(
                run.model_copy(
                    update={
                        "safe_boundary": "definition_changed"
                        if reason.startswith("definition_changed")
                        else "recovery_blocked",
                        "recovery_reason": reason,
                    }
                )
            )
        return await self._append_agent(
            agent,
            status="recovery_blocked",
            active_run_id=None,
        )

    async def _has_pending_newer_than(
        self,
        agent: AgentSessionRecord,
        started_at: str | None,
    ) -> bool:
        if started_at is None:
            return False
        persistence = await self._load_persistence(agent)
        return any(
            entry.timestamp > started_at
            for entry in persistence.get_unacked_user_messages()
        )

    async def _spawn_outcome(
        self,
        owner_session_id: str,
        agent_id: str,
        run_id: str | None,
        *,
        result: AgentRunResult | None = None,
    ) -> SubagentSpawnOutcome:
        persisted_agent = await self.records.get_session(
            session_id=owner_session_id,
            agent_id=agent_id,
        )
        persisted_run = (
            await self.records.get_run(session_id=owner_session_id, run_id=run_id)
            if run_id
            else None
        )
        if persisted_agent is None:
            raise SubagentCollaborationError("subagent projection is unavailable")
        return SubagentSpawnOutcome(
            agent=persisted_agent,
            run=persisted_run,
            result=result,
        )


def build_definition_snapshot(launch: SubagentRunLaunch) -> dict[str, Any]:
    manifest = launch.manifest.model_dump(
        mode="json",
        by_alias=True,
        exclude_none=True,
    )
    if launch.manifest.allowed_tools is None:
        allowed_tools = sorted(launch.tool_registry.names())
    else:
        allowed_tools = sorted(
            expand_allowed_tool_names(
                launch.manifest.allowed_tools,
                launch.tool_registry.names(),
            )
            or ()
        )
    schemas = launch.tool_registry.list_for_llm(allowed_tools)
    model = launch.manifest.model or getattr(launch.executor, "_default_model", None) or ""
    return _json_safe(
        {
            "manifest": manifest,
            "model": model,
            "allowed_tools": allowed_tools,
            "tool_schemas": schemas,
            "overrides": launch.overrides,
        }
    )


def _fingerprint(snapshot: dict[str, Any]) -> str:
    payload = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _deterministic_agent_id(session_id: str, request_id: str, agent_name: str) -> str:
    if not request_id:
        return new_agent_id()
    value = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"prismclaw:subagent:{session_id}:{request_id}:{agent_name}",
    )
    return f"ag:{value}"


def _deterministic_message_id(agent_id: str, request_id: str) -> str:
    value = uuid.uuid5(uuid.NAMESPACE_URL, f"prismclaw:subagent-send:{agent_id}:{request_id}")
    return f"msg:{value}"


def _find_message(
    persistence: SubagentPersistence,
    *,
    message_id: str,
) -> MessageEntry | None:
    for entry in persistence.tree.get_branch():
        if (
            isinstance(entry, MessageEntry)
            and entry.role == "user"
            and (entry.metadata or {}).get("message_id") == message_id
        ):
            return entry
    return None


def _latest_task_text(persistence: SubagentPersistence) -> str:
    pending = persistence.get_unacked_user_messages()
    if pending:
        content = pending[-1].content
        return content if isinstance(content, str) else str(content)
    return "Continue from the persisted child session without replaying completed tool calls."
