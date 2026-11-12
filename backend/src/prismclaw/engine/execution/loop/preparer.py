"""RunPreparer — run_agent_stream 启动路径判定。

将 run_agent_stream 中 ~1060 行启动逻辑（6 条路径交织）提取为独立的
RunPreparer.prepare()，返回判别类型 PrepareResult：

- EarlyExit：hook 阻断 / bootstrap 错误 / 确认拒绝等早期退出，携带事件流
- PreparedRun：正常启动完成，所有字段有确定初始值，杜绝 finally 的 NameError

调用方只需 isinstance 判别，不再需要 try/except NameError 守护。
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from prismclaw.engine.execution.request.attachments import route_attachments_for_turn
from prismclaw.engine.security.gate.governance import (
    build_review_requirements,
    build_task_context_contract,
    build_turn_semantics,
    make_ledger_event,
)
from prismclaw.engine.security.gate.probes import run_recovery_probe
from prismclaw.engine.execution.dispatch.response_handling import (
    complete_no_tool_turn,
)
from prismclaw.engine.compression.preservation import (
    project_active_skill_invocation,
    project_preservation_runtime,
)
from prismclaw.engine.execution.loop.arbiter import (
    RunContext,
    persist_snapshot_fn,
    mark_task_blocked,
)
from prismclaw.engine.execution.control.kernel import RecoveryPolicy, TerminalReason
from prismclaw.schema.agent.run_control_types import RunControl
from prismclaw.engine.execution.checkpoint import (
    ResumeBoundary,
    RunSnapshot,
    RunStateMachine,
    evaluate_resume_decision,
    is_recovery_probe_resumable,
    load_run_snapshot_for_tree,
    recovery_probe_required,
)
from prismclaw.engine.toolkit.registry.tool_runtime import (
    ToolContext,
)
from prismclaw.engine.toolkit.file.workspace import (
    WorkspaceBoundaryError,
    WorkspaceResolver,
)
from prismclaw.infra.workspace_paths import session_dir_in
from prismclaw.schema.config.config import WorkspaceConfig
from prismclaw.engine.foundation.telemetry import HotPathTelemetry, TurnProfiler
from prismclaw.engine.hooks import (
    HookEvent,
    _log_hook_outcome,
)
from prismclaw.schema.content.workspace import SandboxTier
from prismclaw.engine.plugins.hooks.schema import (
    HookDecision,
)
from prismclaw.schema.agent.session import MessageEntry
from prismclaw.schema.agent.task_runtime import ConfirmationDecision
from prismclaw.schema.content.streaming import ErrorEvent
from prismclaw.schema.session.types import SessionTree, generate_entry_id, now_iso
from prismclaw.schema.agent.session import SystemReminderProjection
from prismclaw.schema.services.skills import SkillInvocation, SkillInvocationState

from prismclaw.engine.execution.loop.bootstrap import (
    EntryRuntimeBootstrapError,
    AgentRunnerDeps,
    ensure_session,
    _append,
    _build_task_runtime_from_spec,
    _bootstrap_entry_runtime,
    _persist_task_spec_snapshot,
    _persist_task_runtime_snapshot,
    _persist_review_requirements_snapshot,
)
from prismclaw.engine.execution.state.task_persistence import (
    _append_decision_tail,
    _build_runtime_mutation_patch,
    _build_task_terminal_mutation,
    _evaluate_and_apply_task_mutation,
    _new_task_mutation,
    _persist_decision_ledger_event,
    _persist_execution_evidence,
    _set_resume_boundary,
)
from prismclaw.engine.execution.state.streaming import (
    _apply_fresh_governance_fields,
)
from prismclaw.engine.execution.loop.bootstrap import (
    _build_manual_confirmation_evidence,
    _build_preflight_probe_evidence,
    _build_recovery_probe_evidence,
    _build_workspace_state_evidence,
)
from prismclaw.engine.execution.loop.pending_queue import PendingMessageQueue
from prismclaw.engine.execution.loop.session_manager import (
    setup_trajectory_early,
    TrajectoryScope,
)
from prismclaw.engine.execution.loop.ownership import (
    _claim_run_ownership,
    _restore_review_requirements_from_snapshot,
    _restore_task_spec_from_snapshot,
    _RunOwnershipLease,
    _takeover_run_ownership,
)
from prismclaw.schema.agent.types import RunRequest

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# PrepareResult: discriminated union
# ---------------------------------------------------------------------------

@dataclass
class EarlyExit:
    """Hook 阻断等早期退出路径，携带待 yield 的事件流 + teardown 所需状态。"""
    events: AsyncIterator[Any]
    # teardown 需要的状态（EarlyExit 路径已做了部分初始化）
    ctx: RunContext | None = None
    tree: SessionTree | None = None
    snapshot: RunSnapshot | None = None
    tool_ctx: ToolContext | None = None
    ownership_lease: _RunOwnershipLease | None = None
    profiler: TurnProfiler | None = None
    active_skill_invocation: SkillInvocationState | None = None
    initial_task_spec: Any = None
    run_state: Any = None
    iteration: int = 0


@dataclass
class PreparedRun:
    """run_agent_stream 启动阶段的确定性产物。所有字段均有确定初始值。"""

    # ── 核心对象 ──
    tree: SessionTree | None = None
    snapshot: RunSnapshot | None = None
    task_runtime: Any = None
    initial_task_spec: Any = None

    # ── 状态标记（全部有默认值，杜绝 NameError）──
    terminated_by: TerminalReason = TerminalReason.DONE
    iteration: int = 0

    # ── 运行时上下文 ──
    ctx: RunContext | None = None
    tool_ctx: ToolContext | None = None
    active_skill_invocation: SkillInvocationState | None = None
    ownership_lease: _RunOwnershipLease | None = None

    # ── 附件决策（guidance 由 runner 读取）──
    attachment_decision: Any = None

    # ── 轨迹 ──
    traj_scope: TrajectoryScope | None = None

    # ── 性能 ──
    profiler: TurnProfiler | None = None
    # 回合级计时聚合（loop 结束后由 runner 回填，仅供 teardown 汇总）
    perf: Any = None

    # ── 早期 exit 仍需的状态 ──
    recovery: RecoveryPolicy | None = None
    state_machine: RunStateMachine | None = None
    effective_model: str | None = None
    run_state: Any = None
    evidence_ledger: list[Any] = field(default_factory=list)
    pending_messages: list[Any] = field(default_factory=list)
    prompt_cache_runtime: Any = None
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_creation_tokens: int = 0
    total_cache_read_tokens: int = 0
    _mode: str = "chat"

    # ── 运行时标记（loop 后 sync-back 用）──
    latest_review_decision: Any = None
    llm_start_monotonic: float = 0.0


# 联合类型：调用方 isinstance(EarlyExit | PreparedRun) 判别
PrepareResult = EarlyExit | PreparedRun


# ---------------------------------------------------------------------------
# RunPreparer
# ---------------------------------------------------------------------------

class RunPreparer:
    """封装 run_agent_stream 的全部启动路径判定。

    6 条启动路径：
    1. 新任务：_bootstrap_entry_runtime() 构建运行时
    2. 恢复模式：recovery_mode=True → 加载快照 → 校验 run_id → 替换 request
    3. 确认继续：existing_task_runtime.state == "awaiting_confirmation" + confirmation_decision
    4. 确认拒绝：同上但 decision=deny
    5. Hook 阻断：HookDecision.BLOCK → mark_task_blocked() → complete_no_tool_turn() → EarlyExit
    6. 技能/插件激活：active_skill_invocation 非空

    全部分支在 prepare() 内闭合，不泄漏到外部。
    """

    def __init__(
        self,
        request: RunRequest,
        deps: AgentRunnerDeps,
        *,
        tool_workspace_path: str | Path,
        control: RunControl | None = None,
    ):
        self._request = request
        self._deps = deps
        self._tool_workspace_path = Path(tool_workspace_path).resolve()
        self._control = control or RunControl()

    async def prepare(self) -> PrepareResult:
        """执行启动路径判定，返回 PrepareResult。

        - EarlyExit：hook 阻断等早期退出，调用方直接 yield events
        - PreparedRun：正常启动完成，调用方进入主循环
        """
        request = self._request
        deps = self._deps
        resolved_ws_path = self._tool_workspace_path
        abort_event = self._control.abort_event

        from prismclaw.engine.execution.control.kernel import resolve_effort_budget
        _effort_budget = resolve_effort_budget(
            effort=getattr(request, "effort", "standard") or "standard",
            long_running=bool(getattr(request, "long_running", False)),
            tiers=deps.config.effort,
        )
        run_deadline_s = _effort_budget.hard_s
        run_started = time.monotonic()
        terminated_by = TerminalReason.DONE

        # 早期退出（hook 阻断等）可能发生在这些对象构造之前：统一预置为
        # None，EarlyExit 直接携带，不再用 `in dir()` 探测变量是否已绑定。
        initial_task_spec: Any = None
        run_state: Any = None
        tool_ctx: ToolContext | None = None
        ownership_lease: _RunOwnershipLease | None = None

        profiler = TurnProfiler(
            session_id=request.session_id,
            request_id=request.request_id,
        )
        hotpath = HotPathTelemetry(profiler=profiler)

        # ── RunContext ──
        ctx = RunContext(
            deps=deps,
            request=request,
            abort_event=abort_event,
            hotpath=hotpath,
            terminated_by=terminated_by,
            run_deadline_s=run_deadline_s,
            run_started=run_started,
        )

        tree = await ensure_session(
            deps,
            session_id=request.session_id,
            workspace_id=request.workspace_id,
            agent_id=request.agent_id,
        )
        snapshot = load_run_snapshot_for_tree(tree)
        ctx.tree = tree
        ctx.snapshot = snapshot
        prompt_cache_runtime = deps.prompt_cache_runtime_contract
        active_skill_invocation = snapshot.invoked_skill_state.active_invocation
        recovery_mode = bool(request.recovery_run_id)
        recovered_task_spec = _restore_task_spec_from_snapshot(snapshot) if recovery_mode else None
        recovered_review_requirements = (
            _restore_review_requirements_from_snapshot(snapshot) if recovery_mode else None
        )
        pending_confirmation_state: str | None = None
        pending_confirmation_response: ConfirmationDecision | None = None
        existing_task_runtime = snapshot.active_context.task_runtime
        existing_task_spec = _restore_task_spec_from_snapshot(snapshot)
        existing_review_requirements = _restore_review_requirements_from_snapshot(snapshot)

        if recovery_mode:
            if snapshot.run_state.run_id != request.recovery_run_id:
                raise RuntimeError(
                    f"recovery snapshot mismatch for session_id={request.session_id}: "
                    f"expected run_id={request.recovery_run_id} actual={snapshot.run_state.run_id}"
                )
            decision = evaluate_resume_decision(snapshot)
            if not is_recovery_probe_resumable(decision):
                raise RuntimeError(
                    f"recovery snapshot is not eligible for takeover: {decision.reason_code}"
                )
            if recovered_task_spec is None or recovered_review_requirements is None:
                raise RuntimeError(
                    f"recovery snapshot missing task/review requirements for run_id={request.recovery_run_id}"
                )
            request.task_spec = recovered_task_spec
            request.task_id = recovered_task_spec.task_id
            request.review_requirements = recovered_review_requirements
            request.user_message = recovered_task_spec.source_message
            request.request_id = request.recovery_run_id

        # ── Hook 收集 ──
        hook_context_injections: list[str] = []

        await deps.hooks.notify_run_start(request.session_id, self._control)
        if deps.hook_dispatcher is not None:
            hook_result = await deps.hook_dispatcher.dispatch(
                HookEvent.SESSION_START,
                {
                    "session_id": request.session_id,
                    "workspace_id": request.workspace_id,
                    "agent_id": request.agent_id,
                    "user_message": request.user_message,
                },
            )
            _log_hook_outcome(HookEvent.SESSION_START, hook_result)
            if hook_result.additional_context:
                hook_context_injections.append(hook_result.additional_context)
            if hook_result.decision == HookDecision.BLOCK:
                logger.warning(
                    "SessionStart hook returned block decision: %s",
                    hook_result.decision_reason,
                )

        effective_model = request.model or tree.header.model_override or deps.llm.default_model
        attachment_decision = await asyncio.to_thread(
            route_attachments_for_turn,
            user_message=request.user_message,
            display_user_message=request.display_user_message,
            raw_attachments=request.attachments,
            workspace_path=resolved_ws_path,
            context_budget=deps.config.context.budget,
        )
        if attachment_decision.downgraded_image_count > 0:
            logger.info(
                "routing %d image attachment(s) to read_image tool notes for %s",
                attachment_decision.downgraded_image_count,
                effective_model,
            )
        user_entry_content = attachment_decision.display_content
        user_llm_content = attachment_decision.llm_content

        # ── Trajectory bus + auditor: early setup ──
        _traj_scope = setup_trajectory_early(tree, request, deps)

        if not recovery_mode:
            user_entry = MessageEntry(
                id=generate_entry_id(set(tree.entries.keys())),
                parent_id=tree.leaf_id,
                timestamp=now_iso(),
                role="user",
                content=user_entry_content,
                enriched_content=user_llm_content if user_llm_content != user_entry_content else None,
            )
            await _append(deps, tree, user_entry)

            # ── UserPromptSubmit hook ──
            if deps.hook_dispatcher is not None:
                hook_result = await deps.hook_dispatcher.dispatch(
                    HookEvent.USER_PROMPT_SUBMIT,
                    {
                        "session_id": request.session_id,
                        "workspace_id": request.workspace_id,
                        "agent_id": request.agent_id,
                        "user_message": request.user_message,
                        "attachments": request.attachments,
                    },
                )
                _log_hook_outcome(HookEvent.USER_PROMPT_SUBMIT, hook_result)
                if hook_result.additional_context:
                    hook_context_injections.append(hook_result.additional_context)
                if hook_result.decision == HookDecision.BLOCK:
                    block_reason = (
                        hook_result.decision_reason
                        or hook_result.stop_reason
                        or "提示构建被插件钩子阻断"
                    )
                    await mark_task_blocked(ctx, block_reason)
                    task_runtime = ctx.task_runtime
                    done_event = await complete_no_tool_turn(
                        deps=deps,
                        request=request,
                        tree=tree,
                        session_messages=[
                            {"role": "user", "content": request.user_message}
                        ],
                        append_entry=_append,
                        response_text=f"[plugin hook] {block_reason}",
                        usage_totals={},
                    )
                    # ── EarlyExit path #1: Hook block ──
                    return EarlyExit(
                    events=_single_event_iter(done_event),
                    ctx=ctx,
                    tree=tree,
                    snapshot=snapshot,
                    profiler=profiler,
                    active_skill_invocation=active_skill_invocation,
                    initial_task_spec=initial_task_spec,
                    run_state=run_state,
                )

            request.request_id = request.request_id or f"req_{generate_entry_id(set())}"

            # ── Confirmation handling ──
            if (
                existing_task_runtime is not None
                and existing_task_spec is not None
                and existing_review_requirements is not None
                and existing_task_runtime.state in {"awaiting_confirmation", "awaiting_risk_confirmation"}
                and request.confirmation_decision is not None
            ):
                pending_confirmation_state = existing_task_runtime.state
                pending_confirmation_response = request.confirmation_decision
                initial_task_spec = existing_task_spec
                request.task_id = initial_task_spec.task_id
                request.task_spec = initial_task_spec
                request.turn_semantics = build_turn_semantics(
                    user_message=request.user_message,
                    tree=tree,
                )
                request.task_context_contract = build_task_context_contract(
                    task_spec=initial_task_spec,
                    turn_semantics=request.turn_semantics,
                )
                request.review_requirements = build_review_requirements(
                    task_context_contract=request.task_context_contract,
                )
                if request.review_requirements is None:
                    request.review_requirements = existing_review_requirements
            else:
                initial_task_spec = await _apply_fresh_governance_fields(
                    deps=deps,
                    request=request,
                    tree=tree,
                    tool_workspace_path=resolved_ws_path,
                )
        else:
            initial_task_spec = request.task_spec

        latest_review_decision = tree.get_latest_review_decision()
        persisted_task_runtime = tree.get_latest_task_runtime()
        task_runtime = snapshot.active_context.task_runtime
        if (
            task_runtime is None or task_runtime.task_id != initial_task_spec.task_id
        ) and persisted_task_runtime is not None and persisted_task_runtime.task_id == initial_task_spec.task_id:
            task_runtime = persisted_task_runtime

        bootstrapped_runtime_for_new_task = False
        try:
            if (
                not recovery_mode
                and pending_confirmation_state is None
                and (task_runtime is None or task_runtime.task_id != initial_task_spec.task_id)
            ):
                task_runtime = await _bootstrap_entry_runtime(
                    deps=deps,
                    tree=tree,
                    task_spec=initial_task_spec,
                    review_requirements=request.review_requirements,
                    workspace_id=request.workspace_id,
                    persist_runtime=False,
                )
                bootstrapped_runtime_for_new_task = True
            if recovery_mode and pending_confirmation_state is None and task_runtime is None:
                task_runtime = await _bootstrap_entry_runtime(
                    deps=deps,
                    tree=tree,
                    task_spec=initial_task_spec,
                    review_requirements=request.review_requirements,
                    workspace_id=request.workspace_id,
                )
                bootstrapped_runtime_for_new_task = True
        except EntryRuntimeBootstrapError as exc:
            logger.warning(
                "entry_startup_unavailable session_id=%s task_id=%s reason_code=%s",
                request.session_id,
                initial_task_spec.task_id,
                exc.reason_code,
            )
            if task_runtime is not None and task_runtime.task_id == initial_task_spec.task_id:
                failed_runtime = task_runtime.model_copy(
                    update={
                        "entry_resolution_status": "failed",
                        "entry_failure_code": exc.error_code,
                        "updated_at_epoch_ms": int(time.time() * 1000),
                    }
                )
                await _persist_task_runtime_snapshot(deps, tree, task_runtime=failed_runtime)
            # ── EarlyExit path #2: Bootstrap error ──
            return EarlyExit(
                events=_single_event_iter(
                    ErrorEvent(error_code=exc.error_code, message=exc.user_message)
                ),
                ctx=ctx,
                tree=tree,
                snapshot=snapshot,
                profiler=profiler,
                active_skill_invocation=active_skill_invocation,
                initial_task_spec=initial_task_spec,
            )

        if task_runtime is None or task_runtime.task_id != initial_task_spec.task_id:
            task_runtime = _build_task_runtime_from_spec(
                task_spec=initial_task_spec,
                workspace_id=request.workspace_id,
            )
        else:
            task_runtime = _build_task_runtime_from_spec(
                task_spec=initial_task_spec,
                workspace_id=request.workspace_id,
                prior_runtime=task_runtime,
                entry_resolution_status="ready",
            )

        evidence_ledger = tree.get_evidence_ledger()
        ctx.evidence_ledger = evidence_ledger

        run_state = snapshot.run_state
        if not recovery_mode:
            run_state.run_id = request.request_id
            # 新 run 边界：复位"本 run"语义的台账字段（run 生命周期
            # 护栏，sdd-convergence-efficiency D1 复核发现 F1/F2）。
            # recovery 保持同 run_id，台账延续（续跑语义）。
            # convergence_advisory_sent：收敛 advisory 每 run 至多一次，
            # 跨任务残留会让新任务首回合误判"已提示过"。
            # 注意：RunState.tool_loop_state 是 dict（checkpoint
            # 持久化层），runner 加载时才经 from_dict 还原为对象。
            _tls = snapshot.run_state.tool_loop_state
            if _tls:
                _tls["convergence_advisory_sent"] = False
        snapshot.worker_id = getattr(deps, "worker_id", None)
        run_state.current_phase = "prepare"

        if not recovery_mode and pending_confirmation_state is None:
            if not bootstrapped_runtime_for_new_task:
                await _persist_task_spec_snapshot(deps, tree, task_spec=initial_task_spec)
            try:
                await _persist_task_runtime_snapshot(deps, tree, task_runtime=task_runtime)
            except Exception as exc:
                logger.warning(
                    "entry_runtime_persist_failed session_id=%s task_id=%s reason=%s",
                    request.session_id,
                    initial_task_spec.task_id,
                    getattr(exc, "reason_code", "snapshot_persist_failed"),
                )
                # ── EarlyExit path #3: Persist failure ──
                return EarlyExit(
                    events=_single_event_iter(
                        ErrorEvent(
                            error_code="entry_runtime_bootstrap_failed",
                            message="入口运行时引导失败",
                        )
                    ),
                    ctx=ctx,
                    tree=tree,
                    snapshot=snapshot,
                    profiler=profiler,
                    active_skill_invocation=active_skill_invocation,
                    initial_task_spec=initial_task_spec,
                    run_state=run_state,
                )
            if not bootstrapped_runtime_for_new_task:
                await _persist_review_requirements_snapshot(
                    deps,
                    tree,
                    review_requirements=request.review_requirements,
                )

            # ── Preflight probe ──
            preflight_probe_event = None
            if initial_task_spec.preflight_probe is not None:
                preflight_probe_evidence = _build_preflight_probe_evidence(
                    task_runtime=task_runtime,
                )
                workspace_state_evidence = _build_workspace_state_evidence(
                    task_runtime=task_runtime,
                )
                await _persist_execution_evidence(
                    deps,
                    tree,
                    evidence=preflight_probe_evidence,
                )
                await _persist_execution_evidence(
                    deps,
                    tree,
                    evidence=workspace_state_evidence,
                )
                evidence_ledger.append(preflight_probe_evidence)
                evidence_ledger.append(workspace_state_evidence)
                preflight_probe_event = make_ledger_event(
                    session_id=request.session_id,
                    task_id=initial_task_spec.task_id,
                    turn_index=initial_task_spec.turn_index,
                    event_type="preflight_probe_completed",
                    summary=initial_task_spec.preflight_probe.summary,
                    actor="preflight_probe",
                    payload={
                        "request_id": request.request_id,
                        "baseline_risk": initial_task_spec.preflight_probe.baseline_risk,
                        "mentioned_paths": initial_task_spec.preflight_probe.mentioned_paths,
                        "existing_paths": initial_task_spec.preflight_probe.existing_paths,
                        "touched_roots": initial_task_spec.preflight_probe.touched_roots,
                        "workspace_file_count": initial_task_spec.preflight_probe.workspace_file_count,
                        "workspace_dir_count": initial_task_spec.preflight_probe.workspace_dir_count,
                        "code_file_count": initial_task_spec.preflight_probe.code_file_count,
                        "git_dirty_paths": initial_task_spec.preflight_probe.git_dirty_paths,
                        "critical_workspace_paths": initial_task_spec.preflight_probe.critical_workspace_paths,
                        "signals": [
                            signal.model_dump(mode="json")
                            for signal in initial_task_spec.preflight_probe.signals
                        ],
                    },
                )
                await _persist_decision_ledger_event(
                    deps,
                    tree,
                    event=preflight_probe_event,
                )

            task_interpreted_event = make_ledger_event(
                session_id=request.session_id,
                task_id=initial_task_spec.task_id,
                turn_index=initial_task_spec.turn_index,
                event_type="task_interpreted",
                summary=f"Task spec resolved (risk={initial_task_spec.risk_level}).",
                payload={
                    "request_id": request.request_id,
                    "risk_level": initial_task_spec.risk_level,
                    "constraints": initial_task_spec.constraints,
                },
            )
            await _persist_decision_ledger_event(deps, tree, event=task_interpreted_event)

            review_created_event = make_ledger_event(
                session_id=request.session_id,
                task_id=initial_task_spec.task_id,
                turn_index=initial_task_spec.turn_index,
                event_type="review_requirements_created",
                summary="已创建审查要求契约。",
                payload={
                    "request_id": request.request_id,
                    "require_human_review": request.review_requirements.require_human_review,
                    "require_permission_review": request.review_requirements.require_permission_review,
                    "require_fact_evidence": request.review_requirements.require_fact_evidence,
                    "require_tool_verification": request.review_requirements.require_tool_verification,
                },
            )
            await _persist_decision_ledger_event(deps, tree, event=review_created_event)

            snapshot.active_context = snapshot.active_context.model_copy(
                update={
                    "request_id": request.request_id,
                    "task_id": initial_task_spec.task_id,
                    "task_spec": initial_task_spec,
                    "task_runtime": task_runtime,
                    "review_requirements": request.review_requirements,
                    "decision_ledger_tail": [],
                }
            )
            if preflight_probe_event is not None:
                _append_decision_tail(snapshot, preflight_probe_event.event_id)
            _append_decision_tail(snapshot, task_interpreted_event.event_id)
            _append_decision_tail(snapshot, review_created_event.event_id)

        else:
            # ── Recovery mode ──
            decision = evaluate_resume_decision(snapshot)
            recovery_probe_result = run_recovery_probe(
                task_spec=initial_task_spec,
                workspace_path=resolved_ws_path,
                preflight_probe=initial_task_spec.preflight_probe,
            )
            task_runtime = task_runtime.model_copy(
                update={
                    "state": "resuming",
                    "pending_confirmation_reason": None,
                }
            )
            await _persist_task_runtime_snapshot(deps, tree, task_runtime=task_runtime)
            recovery_probe_evidence = _build_recovery_probe_evidence(
                task_runtime=task_runtime,
            )
            await _persist_execution_evidence(deps, tree, evidence=recovery_probe_evidence)
            evidence_ledger.append(recovery_probe_evidence)

            recovery_probe_event = make_ledger_event(
                session_id=request.session_id,
                task_id=initial_task_spec.task_id,
                turn_index=initial_task_spec.turn_index,
                event_type="recovery_probe_completed",
                summary=recovery_probe_result.summary,
                actor="recovery_probe",
                payload={
                    "request_id": request.request_id,
                    "baseline_source": recovery_probe_result.baseline_source,
                    "status": recovery_probe_result.status,
                    "checked_paths": recovery_probe_result.checked_paths,
                    "missing_paths": recovery_probe_result.missing_paths,
                    "signals": [signal.model_dump(mode="json") for signal in recovery_probe_result.signals],
                },
            )
            await _persist_decision_ledger_event(deps, tree, event=recovery_probe_event)

            takeover_event = make_ledger_event(
                session_id=request.session_id,
                task_id=initial_task_spec.task_id,
                turn_index=initial_task_spec.turn_index,
                event_type="run_takeover_started",
                summary=(
                    f"Worker {getattr(deps, 'worker_id', 'unknown')} is resuming run "
                    f"{request.recovery_run_id} after owner {request.recovery_owner_worker_id} became unavailable."
                ),
                payload={
                    "request_id": request.request_id,
                    "recovery_reason": request.recovery_reason or "admin_takeover",
                    "previous_owner_worker_id": request.recovery_owner_worker_id,
                    "resume_disposition": decision.disposition.value,
                    "resume_reason_code": decision.reason_code,
                    "recovery_probe_required": recovery_probe_required(decision),
                },
            )
            await _persist_decision_ledger_event(deps, tree, event=takeover_event)

            if recovery_probe_result.status == "blocked":
                recovery_mutation = _new_task_mutation(
                    task_runtime,
                    actor_kind="recovery",
                    mutation_type="task.block",
                    patch=_build_runtime_mutation_patch(
                        task_runtime,
                        extras={"reason": recovery_probe_result.summary},
                    ),
                    evidence_ids=[recovery_probe_evidence.evidence_id],
                    caused_by_event_id=recovery_probe_event.event_id,
                )
            else:
                recovery_mutation = _new_task_mutation(
                    task_runtime,
                    actor_kind="recovery",
                    mutation_type="task.resume_probe_completed",
                    patch=_build_runtime_mutation_patch(
                        task_runtime,
                        extras={
                            "resume_state": (
                                "awaiting_confirmation"
                                if recovery_probe_result.status == "degraded"
                                else "executing"
                            ),
                            "reason": (
                                recovery_probe_result.summary
                                if recovery_probe_result.status == "degraded"
                                else None
                            ),
                        },
                    ),
                    evidence_ids=[recovery_probe_evidence.evidence_id],
                    caused_by_event_id=recovery_probe_event.event_id,
                )

            task_runtime, accepted, reason_code = await _evaluate_and_apply_task_mutation(
                deps=deps,
                tree=tree,
                snapshot=snapshot,
                task_runtime=task_runtime,
                mutation=recovery_mutation,
                evidence_records=evidence_ledger,
            )
            if not accepted:
                raise RuntimeError(f"recovery probe mutation rejected: {reason_code}")

            snapshot.active_context = snapshot.active_context.model_copy(
                update={
                    "request_id": request.request_id,
                    "task_id": initial_task_spec.task_id,
                    "task_spec": initial_task_spec,
                    "task_runtime": task_runtime,
                    "review_requirements": request.review_requirements,
                }
            )
            _append_decision_tail(snapshot, recovery_probe_event.event_id)
            _append_decision_tail(snapshot, takeover_event.event_id)

        # ── RecoveryPolicy + confirmation handling ──
        # 恢复链载体（初始值取自 run_state.recovery_state，内核与压缩重启
        # 共用同一份链）。
        recovery = RecoveryPolicy(
            limit=int(deps.config.run_loop.arbiter_recovery_chain_limit),
            chain=run_state.recovery_state.chain,
        )
        ctx.recovery = recovery

        confirmation_messages = [{"role": "user", "content": request.user_message}]

        if pending_confirmation_state is not None:
            if pending_confirmation_response is None:
                # 用户发送了新消息但未提供确认决策，视为放弃旧任务、启动新任务。
                logger.info(
                    "pending_confirmation_cleared: old_task_state=%s, starting_new_task",
                    pending_confirmation_state,
                )
                block_mutation = _new_task_mutation(
                    task_runtime,
                    actor_kind="system",
                    mutation_type="task.block",
                    patch=_build_runtime_mutation_patch(
                        task_runtime,
                        extras={"reason": "用户发送新消息，放弃确认"},
                    ),
                )
                task_runtime, _blocked, _block_reason = (
                    await _evaluate_and_apply_task_mutation(
                        deps=deps,
                        tree=tree,
                        snapshot=snapshot,
                        task_runtime=task_runtime,
                        mutation=block_mutation,
                        evidence_records=evidence_ledger,
                    )
                )
                pending_confirmation_state = None
                pending_confirmation_response = None

            elif pending_confirmation_response == "deny":
                deny_reason = (
                    "用户拒绝风险确认，任务已停止。"
                    if pending_confirmation_state == "awaiting_risk_confirmation"
                    else "用户拒绝人工确认，任务已停止。"
                )
                block_mutation = _new_task_mutation(
                    task_runtime,
                    actor_kind="system",
                    mutation_type="task.block",
                    patch=_build_runtime_mutation_patch(
                        task_runtime,
                        extras={"reason": deny_reason},
                    ),
                )
                task_runtime, _accepted, _reason = await _evaluate_and_apply_task_mutation(
                    deps=deps,
                    tree=tree,
                    snapshot=snapshot,
                    task_runtime=task_runtime,
                    mutation=block_mutation,
                    evidence_records=evidence_ledger,
                )
                _set_resume_boundary(snapshot, boundary=ResumeBoundary.BEFORE_MODEL_CALL)
                await persist_snapshot_fn(ctx)
                done_event = await complete_no_tool_turn(
                    deps=deps,
                    request=request,
                    tree=tree,
                    session_messages=confirmation_messages,
                    append_entry=_append,
                    response_text=deny_reason,
                    usage_totals={},
                )
                # ── EarlyExit path #4: Confirmation deny ──
                return EarlyExit(
                    events=_single_event_iter(done_event),
                    ctx=ctx,
                    tree=tree,
                    snapshot=snapshot,
                    tool_ctx=tool_ctx,
                    ownership_lease=ownership_lease,
                    profiler=profiler,
                    active_skill_invocation=active_skill_invocation,
                    initial_task_spec=initial_task_spec,
                    run_state=run_state,
                )

            elif pending_confirmation_state == "awaiting_risk_confirmation":
                resume_mutation = _new_task_mutation(
                    task_runtime,
                    actor_kind="system",
                    mutation_type="task.risk_confirmed",
                    patch=_build_runtime_mutation_patch(task_runtime),
                )
                task_runtime, _accepted, _reason = await _evaluate_and_apply_task_mutation(
                    deps=deps,
                    tree=tree,
                    snapshot=snapshot,
                    task_runtime=task_runtime,
                    mutation=resume_mutation,
                    evidence_records=evidence_ledger,
                )

            else:
                latest_review = tree.get_latest_review_decision()
                if (
                    latest_review is not None
                    and latest_review.decision_id == task_runtime.latest_review_decision_id
                ):
                    manual_evidence = _build_manual_confirmation_evidence(
                        task_runtime=task_runtime,
                    )
                    await _persist_execution_evidence(deps, tree, evidence=manual_evidence)
                    evidence_ledger.append(manual_evidence)
                    terminal_mutation = _build_task_terminal_mutation(
                        task_runtime,
                        actor_kind="review",
                        evidence_ids=[manual_evidence.evidence_id],
                    )
                    task_runtime, accepted, reason_code = await _evaluate_and_apply_task_mutation(
                        deps=deps,
                        tree=tree,
                        snapshot=snapshot,
                        task_runtime=task_runtime,
                        mutation=terminal_mutation,
                        evidence_records=evidence_ledger,
                    )
                    if not accepted:
                        raise RuntimeError(f"manual confirmation completion rejected: {reason_code}")
                    _set_resume_boundary(snapshot, boundary=ResumeBoundary.AFTER_TURN_FINALIZE)
                    await persist_snapshot_fn(ctx)
                    done_event = await complete_no_tool_turn(
                        deps=deps,
                        request=request,
                        tree=tree,
                        session_messages=confirmation_messages,
                        append_entry=_append,
                        response_text="已记录人工签署，当前任务按最新审查结果完成。",
                        usage_totals={},
                    )
                    # ── EarlyExit path #5: Manual confirmation complete ──
                    return EarlyExit(
                        events=_single_event_iter(done_event),
                        ctx=ctx,
                        tree=tree,
                        snapshot=snapshot,
                        tool_ctx=tool_ctx,
                        ownership_lease=ownership_lease,
                        profiler=profiler,
                        active_skill_invocation=active_skill_invocation,
                        initial_task_spec=initial_task_spec,
                        run_state=run_state,
                    )

                resume_mutation = _new_task_mutation(
                    task_runtime,
                    actor_kind="system",
                    mutation_type="task.resume_after_confirmation",
                    patch=_build_runtime_mutation_patch(
                        task_runtime,
                        extras={},
                    ),
                )
                task_runtime, _accepted, _reason = await _evaluate_and_apply_task_mutation(
                    deps=deps,
                    tree=tree,
                    snapshot=snapshot,
                    task_runtime=task_runtime,
                    mutation=resume_mutation,
                    evidence_records=evidence_ledger,
                )

        elif task_runtime.state == "awaiting_risk_confirmation":
            _set_resume_boundary(snapshot, boundary=ResumeBoundary.BEFORE_MODEL_CALL)
            await persist_snapshot_fn(ctx)
            done_event = await complete_no_tool_turn(
                deps=deps,
                request=request,
                tree=tree,
                session_messages=confirmation_messages,
                append_entry=_append,
                response_text=task_runtime.pending_confirmation_reason
                or "当前任务风险较高，请确认后继续执行。",
                usage_totals={},
            )
            # ── EarlyExit path #6: Awaiting risk confirmation ──
            return EarlyExit(
                events=_single_event_iter(done_event),
                ctx=ctx,
                tree=tree,
                snapshot=snapshot,
                tool_ctx=tool_ctx,
                ownership_lease=ownership_lease,
                profiler=profiler,
                active_skill_invocation=active_skill_invocation,
                initial_task_spec=initial_task_spec,
                run_state=run_state,
            )

        _set_resume_boundary(snapshot, boundary=ResumeBoundary.BEFORE_MODEL_CALL)
        await persist_snapshot_fn(ctx)

        if recovery_mode:
            ownership_lease = await _takeover_run_ownership(
                snapshot,
                deps,
                expected_owner_worker_id=request.recovery_owner_worker_id or "",
            )
        else:
            ownership_lease = await _claim_run_ownership(snapshot, deps)

        if request.sandbox_policy is None:
            raise RuntimeError(
                "run_agent_stream requires request.sandbox_policy to be resolved before ToolContext construction"
            )

        # ── Resolve WorkspaceMode + SandboxTier ──
        _mode = getattr(request, "mode_override", None) or "chat"
        _sandbox_tier = getattr(request, "current_sandbox_tier", None) or SandboxTier.STANDARD

        tool_ctx = ToolContext(
            workspace_id=request.workspace_id,
            workspace_path=resolved_ws_path,
            workspace_root=str(resolved_ws_path),
            session_id=request.session_id,
            sandbox_policy=request.sandbox_policy,
            abort=abort_event,
            tenant_id=getattr(request.user_profile, 'tenant_id', None) if getattr(request, 'user_profile', None) else None,
            user_id=request.user_id,
            user_profile=request.user_profile,
            request_id=request.request_id,
            task_id=initial_task_spec.task_id,
            task_spec=initial_task_spec,
            task_runtime=task_runtime,
            review_requirements=request.review_requirements,
            hook_registry=deps.hooks if deps.hooks is not None else None,
            hook_dispatcher=getattr(deps, "hook_dispatcher", None),
            capability_catalog=deps.capability_catalog if deps.capability_catalog is not None else None,
            task_manager=deps.task_manager,
            parallel_task_store=deps.parallel_task_store,
            session_store=deps.session_store,
            session_tree=tree,
            mcp_death_handler=deps.resolved_mcp_death_handler,
            decision_audit_logger=deps.audit_logger,
            mcp_runtime_manager=deps.plugin.runtime_mcp_manager,
            context_engine=deps.context_engine,
            channel=deps.channel,
            headless=deps.session_queue is None,
            mode=_mode,
            current_sandbox_tier=_sandbox_tier,
            runtime=deps.runtime_client,
            workspace_tag=getattr(request, "workspace_tag", None),
        )

        getter = getattr(deps.context_engine, "get_session_state", None)
        if callable(getter):
            try:
                tool_ctx.session_state = getter(request.session_id)
            except Exception:
                logger.debug(
                    "runner.tool_ctx.session_state unavailable session=%s",
                    request.session_id,
                    exc_info=True,
                )

        if deps.resolved_subagent_progress_sink is not None:
            tool_ctx.subagent_progress_sink = deps.resolved_subagent_progress_sink

        for key, value in request.tool_context_extras.items():
            if hasattr(tool_ctx, key):
                setattr(tool_ctx, key, value)

        # ── Skill/plugin activation ──
        if active_skill_invocation is not None:
            project_active_skill_invocation(
                request=request,
                snapshot=snapshot,
                tool_ctx=tool_ctx,
                skill_invocation=active_skill_invocation,
            )

        # ── Command identity projection ──
        _pending_cmd = request.tool_context_extras.get("pending_command_identity")
        if _pending_cmd is not None and active_skill_invocation is None:
            _cmd_invocation = SkillInvocationState(
                skill_name=_pending_cmd["skill_name"],
                plugin_name=_pending_cmd.get("plugin_name"),
                is_active=True,
                clear_after_terminal_result=True,
                invocation_context="command",
            )
            project_active_skill_invocation(
                request=request,
                snapshot=snapshot,
                tool_ctx=tool_ctx,
                skill_invocation=_cmd_invocation,
            )

        # ── Designated skills activation ──
        # 用户本轮显式选了卡片就激活，与既有 active invocation（如持久化插件）
        # 合并在 _merge_invocations 里完成，避免「上轮插件把本轮技能挡住」。
        if request.designated_skills and _pending_cmd is None:
            active_skill_invocation = await self._activate_designated_skills(
                request, deps, resolved_ws_path, snapshot, tool_ctx,
                active_skill_invocation, hook_context_injections,
            )

        # ── Auto-activated skill (gesture / single search match) ──
        # Resolve auto-activation directly here (run start, before the loop)
        # through the same build_skill_execution path as designated skills so
        # the invocation state, allowed-tools policy and skill prompt fragment
        # are all set. Must run in the preparer — the turn-preparation layer
        # executes later (per-iteration) and cannot feed state back here.
        if (
            active_skill_invocation is None
            and _pending_cmd is None
            and not request.designated_skills
        ):
            try:
                from prismclaw.engine.execution.query._turn_helpers import (
                    _try_auto_activate_skill,
                )

                _auto_artifact = _try_auto_activate_skill(
                    user_message=request.user_message,
                    capability_catalog=getattr(deps, "capability_catalog", None),
                    workspace_path=str(resolved_ws_path),
                )
            except Exception:
                _auto_artifact = None  # best-effort; never block run start
            _auto_inv = (
                _auto_artifact.skill_invocation
                if _auto_artifact is not None
                else None
            )
            _auto_skill_name = (
                _auto_inv.skill_name if _auto_inv is not None else None
            )
            if isinstance(_auto_skill_name, str) and _auto_skill_name:
                _auto_invocation = SkillInvocation(
                    skill_name=_auto_skill_name,
                    task_context=request.user_message,
                    source="user",
                )
                try:
                    _skill_rt = getattr(deps, "skill_runtime", None)
                    if _skill_rt is not None:
                        _auto_strategy = _skill_rt.build_skill_execution(
                            _auto_invocation,
                            workspace_path=resolved_ws_path,
                        )
                        if _auto_strategy.invocation_state is not None:
                            project_active_skill_invocation(
                                request=request,
                                snapshot=snapshot,
                                tool_ctx=tool_ctx,
                                skill_invocation=_auto_strategy.invocation_state,
                            )
                            active_skill_invocation = _auto_strategy.invocation_state
                        if _auto_strategy.append_messages:
                            for _am in _auto_strategy.append_messages:
                                _am_content = _am.get("content", "")
                                if _am_content:
                                    hook_context_injections.append(_am_content)
                except LookupError:
                    logger.info(
                        "auto_activated_skill_not_found: skill=%s session=%s",
                        _auto_skill_name,
                        request.session_id,
                    )
                except Exception:
                    logger.warning(
                        "auto_activated_skill_activation_failed: skill=%s session=%s",
                        _auto_skill_name,
                        request.session_id,
                        exc_info=True,
                    )

        # ── Designated plugins activation ──
        if request.designated_plugins:
            active_skill_invocation = await self._activate_designated_plugins(
                request, deps, snapshot, tool_ctx,
                active_skill_invocation, _pending_cmd, hook_context_injections,
            )

        # ── Referenced files injection ──
        # 路径由前端给出（工作区根相对或工具虚拟前缀）。存在性判断必须走文件
        # 工具的同一个解析器，否则 `outputs/x`、`project/x` 这类前缀会被误判：
        # 模型能读到、注入块却标"不存在"。失效路径不阻断请求，只做标注。
        if request.referenced_files:
            _resolver = WorkspaceResolver(
                WorkspaceConfig(workspaces={"default": str(resolved_ws_path)})
            )
            _session_root = session_dir_in(resolved_ws_path, request.session_id)
            _ref_lines: list[str] = []
            for _rf in dict.fromkeys(
                p.strip() for p in request.referenced_files if p.strip()
            ):
                try:
                    _missing = not _resolver.resolve_within(
                        resolved_ws_path, _rf, session_root=_session_root
                    ).exists()
                except WorkspaceBoundaryError:
                    _missing = True
                _ref_lines.append(
                    f"- {_rf}" + ("（文件不存在或已移动）" if _missing else "")
                )
            hook_context_injections.append(
                "用户显式引用了以下工作区文件，请先读取或参考后再回答：\n"
                + "\n".join(_ref_lines)
            )

        project_preservation_runtime(
            request=request,
            snapshot=snapshot,
            tool_ctx=tool_ctx,
        )
        ctx._tool_ctx = tool_ctx
        ctx._tool_workspace_path = resolved_ws_path

        # ── State machine ──
        state_machine = RunStateMachine(run_state)
        iteration = run_state.loop_state.steps_spent
        total_output_tokens = snapshot.total_output_tokens if isinstance(snapshot.total_output_tokens, int) else 0
        total_cache_creation_tokens = snapshot.total_cache_creation_tokens if isinstance(snapshot.total_cache_creation_tokens, int) else 0

        _pending_queue = PendingMessageQueue()

        for _hook_ctx in hook_context_injections:
            _pending_queue.append(SystemReminderProjection.text_reminder(_hook_ctx))

        # ── Trajectory setup_main ──
        _traj_scope.setup_main(request)

        ctx.task_runtime = task_runtime
        ctx.iteration = iteration

        # ── Construct PreparedRun ──
        return PreparedRun(
            tree=tree,
            snapshot=snapshot,
            task_runtime=task_runtime,
            initial_task_spec=initial_task_spec,
            terminated_by=terminated_by,
            iteration=iteration,
            ctx=ctx,
            tool_ctx=tool_ctx,
            active_skill_invocation=active_skill_invocation,
            ownership_lease=ownership_lease,
            attachment_decision=attachment_decision,
            traj_scope=_traj_scope,
            profiler=profiler,
            recovery=recovery,
            state_machine=state_machine,
            effective_model=effective_model,
            run_state=run_state,
            evidence_ledger=evidence_ledger,
            pending_messages=_pending_queue.snapshot() or [],
            prompt_cache_runtime=prompt_cache_runtime,
            total_input_tokens=0,
            total_output_tokens=total_output_tokens,
            total_cache_creation_tokens=total_cache_creation_tokens,
            total_cache_read_tokens=0,
            _mode=_mode,
            latest_review_decision=latest_review_decision,
            llm_start_monotonic=0.0,
        )

    # ── Private helpers for skill/plugin activation ──

    @staticmethod
    def _merge_invocations(
        base: SkillInvocationState | None,
        states: list[SkillInvocationState],
    ) -> SkillInvocationState | None:
        """把多个技能/专家状态合成引擎唯一读取的 active_invocation。

        各自激活时已把 prompt 片段写进注入队列、把记录写进
        ``invoked_skill_state.skills``；这里只合并名称与工具策略：
        - 名称：多个技能用「, 」连接，供 [Active Skill] 与 bash 缓存目录使用；
        - allowed_tools：取并集；任一技能未限制则整体不限制（否则会误锁另
          一个技能需要的工具）。
        """
        ordered: list[SkillInvocationState] = []
        seen: set[str] = set()
        for state in ([base] if base is not None else []) + states:
            if state is None or state.skill_name in seen:
                continue
            seen.add(state.skill_name)
            ordered.append(state)
        if not ordered:
            return None

        skill_names = [
            s.skill_name for s in ordered if not s.skill_name.startswith("_plugin:")
        ]
        plugin_names = [s.plugin_name for s in ordered if s.plugin_name]
        overrides = [s.allowed_tools_override for s in ordered]
        if any(o is None for o in overrides):
            merged_tools = None
        else:
            merged_tools = list(dict.fromkeys(t for o in overrides for t in (o or [])))

        return ordered[0].model_copy(update={
            "skill_name": ", ".join(skill_names) if skill_names else ordered[0].skill_name,
            "plugin_name": plugin_names[0] if plugin_names else ordered[0].plugin_name,
            "allowed_tools_override": merged_tools,
            "is_active": True,
            # 任一技能按轮次清理 → 合成态也清理；专家记录本身是持久的，不受影响。
            "clear_after_terminal_result": any(
                s.clear_after_terminal_result for s in ordered
            ),
            "invocation_context": (
                "skill"
                if any(s.invocation_context == "skill" for s in ordered)
                else ordered[0].invocation_context
            ),
        })

    async def _activate_designated_skills(
        self,
        request: RunRequest,
        deps: AgentRunnerDeps,
        resolved_ws_path: Path,
        snapshot: Any,
        tool_ctx: ToolContext,
        active_skill_invocation: SkillInvocationState | None,
        hook_context_injections: list[str],
    ) -> SkillInvocationState | None:
        """逐个激活 designated_skills，返回合并后的 active_skill_invocation。"""
        from prismclaw.schema.services.skills import SkillInvocation, SkillInvocationState as _SIS

        states: list[SkillInvocationState] = []
        for skill_name in dict.fromkeys(n for n in request.designated_skills if n):
            _desig_invocation = SkillInvocation(
                skill_name=skill_name,
                task_context=request.user_message,
                source="user",
            )
            try:
                _skill_rt = getattr(deps, "skill_runtime", None)
                if _skill_rt is not None:
                    _desig_strategy = _skill_rt.build_skill_execution(
                        _desig_invocation,
                        workspace_path=resolved_ws_path,
                    )
                    if _desig_strategy.invocation_state is not None:
                        states.append(_desig_strategy.invocation_state)
                    if _desig_strategy.append_messages:
                        for _am in _desig_strategy.append_messages:
                            _am_content = _am.get("content", "")
                            if _am_content:
                                hook_context_injections.append(_am_content)
                else:
                    logger.warning(
                        "designated_skill_fallback: skill_runtime unavailable, "
                        "setting invocation state only for skill=%s",
                        skill_name,
                    )
                    states.append(_SIS(
                        skill_name=skill_name,
                        is_active=True,
                        clear_after_terminal_result=True,
                        invocation_context="skill",
                    ))
            except LookupError:
                logger.warning(
                    "designated_skill_not_found: skill=%s session=%s",
                    skill_name,
                    request.session_id,
                )
            except Exception:
                logger.warning(
                    "designated_skill_activation_failed: skill=%s session=%s",
                    skill_name,
                    request.session_id,
                    exc_info=True,
                )

        merged = self._merge_invocations(active_skill_invocation, states)
        if merged is not None:
            project_active_skill_invocation(
                request=request,
                snapshot=snapshot,
                tool_ctx=tool_ctx,
                skill_invocation=merged,
            )
        return merged

    async def _activate_designated_plugins(
        self,
        request: RunRequest,
        deps: AgentRunnerDeps,
        snapshot: Any,
        tool_ctx: ToolContext,
        active_skill_invocation: SkillInvocationState | None,
        _pending_cmd: Any,
        hook_context_injections: list[str],
    ) -> SkillInvocationState | None:
        """逐个激活 designated_plugins，返回与已有技能合并后的 invocation。"""
        from prismclaw.schema.services.skills import SkillInvocationState as _SIS
        from prismclaw.engine.execution.checkpoint.models import InvokedSkillRecord

        # 命令 dispatch 期间不叠加插件（命令的 active invocation 优先）。
        if _pending_cmd is not None:
            return active_skill_invocation
        if (
            active_skill_invocation is not None
            and active_skill_invocation.invocation_context == "command"
        ):
            return active_skill_invocation

        states: list[SkillInvocationState] = []
        for plugin_name in dict.fromkeys(n for n in request.designated_plugins if n):
            try:
                _plugin_rt = getattr(deps, "skill_runtime", None)
                if _plugin_rt is not None:
                    _plugin_instructions = _plugin_rt.get_plugin_instructions(plugin_name)
                    _catalog = getattr(_plugin_rt, "_capability_catalog", None)
                    _plugin_has_skills = False
                    if _catalog is not None:
                        _plugin_skills = [
                            e for e in _catalog.list_skill_entries()
                            if e.plugin_name == plugin_name
                        ]
                        _plugin_has_skills = len(_plugin_skills) > 0
                    if not _plugin_instructions and not _plugin_has_skills:
                        logger.warning(
                            "designated_plugin_unknown: plugin=%s has no instructions "
                            "and no skills in catalog, skipping activation session=%s",
                            plugin_name,
                            request.session_id,
                        )
                        continue

                    if _plugin_instructions:
                        hook_context_injections.append(_plugin_instructions)

                    states.append(_SIS(
                        skill_name=f"_plugin:{plugin_name}",
                        is_active=True,
                        clear_after_terminal_result=False,
                        allowed_tools_override=None,
                        plugin_name=plugin_name,
                        invocation_context="skill",
                    ))

                    _bias_msg = (
                        f"用户指定使用插件「{plugin_name}」。"
                        f"请优先从该插件的技能中选取合适的工具完成任务。"
                    )
                    hook_context_injections.append(_bias_msg)

                    _rendered_fragment_parts = []
                    if _plugin_instructions:
                        _rendered_fragment_parts.append(_plugin_instructions)
                    _rendered_fragment_parts.append(_bias_msg)
                    snapshot.invoked_skill_state.skills[f"_plugin:{plugin_name}"] = InvokedSkillRecord(
                        skill_name=f"_plugin:{plugin_name}",
                        rendered_prompt_fragment="\n\n".join(_rendered_fragment_parts),
                        clear_after_terminal_result=False,
                    )
                else:
                    logger.warning(
                        "designated_plugin_fallback: skill_runtime unavailable, "
                        "setting invocation state only for plugin=%s",
                        plugin_name,
                    )
                    states.append(_SIS(
                        skill_name=f"_plugin:{plugin_name}",
                        is_active=True,
                        clear_after_terminal_result=False,
                        plugin_name=plugin_name,
                        invocation_context="skill",
                    ))
            except Exception:
                logger.warning(
                    "designated_plugin_activation_failed: plugin=%s session=%s",
                    plugin_name,
                    request.session_id,
                    exc_info=True,
                )

        merged = self._merge_invocations(active_skill_invocation, states)
        if merged is not None:
            project_active_skill_invocation(
                request=request,
                snapshot=snapshot,
                tool_ctx=tool_ctx,
                skill_invocation=merged,
            )
        return merged


# ---------------------------------------------------------------------------
# Helper: single-event async iterator
# ---------------------------------------------------------------------------

async def _single_event_iter(event: Any) -> AsyncIterator[Any]:
    """Yield a single event as an async iterator, for EarlyExit paths."""
    yield event
