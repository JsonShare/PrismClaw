from __future__ import annotations

import logging
from dataclasses import dataclass, field
import time
from typing import Any, Awaitable, Callable, Literal, TYPE_CHECKING

from prismclaw.engine.compression.budget_orchestrator import (
    BudgetOrchestrator,
    HistoryCompactionAssessment,
    degrade_context,
    log_degradation_steps,
)
from prismclaw.engine.compression.context_zones import resolve_runtime_zones
from prismclaw.engine.compression.history_compaction import (
    maybe_pretrim_history,
    predict_compaction_trigger,
)
from prismclaw.engine.compression.retention import SectionRetention
from prismclaw.engine.foundation.telemetry.hotpath import HotPathTelemetry
from prismclaw.engine.foundation.metrics import emit
from prismclaw.schema.shared.utils import count_image_parts, estimate_tokens

if TYPE_CHECKING:
    # 类型注解所需的 execution 类型通过 TYPE_CHECKING 引入，
    # runtime 不依赖 execution，避免 compression → execution 反向 import。
    from prismclaw.engine.execution.query.state import QueryLoopState
    from prismclaw.engine.execution.checkpoint import CompressionRuntimeState
    from prismclaw.engine.task_profile.service import TaskCompactionPolicy


# Metric names follow v3 §11.1: {ns}.{comp}.{name}[_{unit}].
_METRIC_CB_FAILURES = "compression.orchestrator.circuit_breaker.failures"
_METRIC_CB_TRIPPED = "compression.orchestrator.circuit_breaker.tripped"
_METRIC_CB_RECOVERED = "compression.orchestrator.circuit_breaker.recovered"

logger = logging.getLogger(__name__)
_HOTPATH = HotPathTelemetry()

PrepareTurnCallable = Callable[..., Awaitable[Any]]
CompactionCallable = Callable[..., Awaitable[Any]]
CompressionExecutionMode = Literal["must_block", "may_defer"]


@dataclass
class CompressionPreparation:
    action: Literal["proceed", "restart_turn"]
    execution_mode: CompressionExecutionMode
    preflight: Any | None
    history_assessment: HistoryCompactionAssessment
    compaction_policy: TaskCompactionPolicy | None
    section_retention: SectionRetention = field(default_factory=SectionRetention.unchanged)
    compression_error_code: str | None = None
    compression_error_message: str | None = None


class CompressionOrchestrator:
    def __init__(
        self,
        *,
        prepare_turn_callable: PrepareTurnCallable | None = None,
        maybe_pretrim_callable: CompactionCallable = maybe_pretrim_history,
        circuit_breaker_threshold: int = 3,
    ) -> None:
        self._prepare_turn_callable = prepare_turn_callable
        self._maybe_pretrim_callable = maybe_pretrim_callable
        self._circuit_breaker_threshold = max(1, circuit_breaker_threshold)

    def _emit_failure_count(self, runtime_state: CompressionRuntimeState) -> None:
        """Publish the current failure count as a gauge sample.

        WP-0.4: emitting on every transition lets dashboards plot the
        ``circuit_breaker.failures`` trace and detect regressions in
        pretrim stability.
        """
        emit(
            _METRIC_CB_FAILURES,
            float(runtime_state.consecutive_failures),
            kind="gauge",
        )

    def _on_recovery(self, runtime_state: CompressionRuntimeState) -> None:
        """Reset internal state and emit a recovery counter sample."""
        if runtime_state.consecutive_failures > 0:
            emit(
                _METRIC_CB_RECOVERED,
                1.0,
                kind="counter",
            )
        runtime_state.consecutive_failures = 0
        self._emit_failure_count(runtime_state)

    def _on_failure(self, runtime_state: CompressionRuntimeState) -> None:
        """Increment failure count and emit gauge."""
        runtime_state.consecutive_failures += 1
        self._emit_failure_count(runtime_state)

    async def prepare_turn(
        self,
        *,
        request: Any,
        deps: Any,
        tree: Any,
        static_result: Any,
        attachment_guidance: str | None,
        query_loop_state: QueryLoopState,
        compression_runtime: CompressionRuntimeState,
        model: str,
        abort_event: Any,
        turn_state: Any | None = None,
        model_max_output: int | None = None,
    ) -> CompressionPreparation:
        started_at = time.monotonic()
        outcome_label = "proceed"
        should_compact = False
        execution_mode: CompressionExecutionMode = "may_defer"
        try:
            message_set = query_loop_state.query_view.messages_for_query
            if self._prepare_turn_callable is None:
                raise RuntimeError("prepare_turn_callable not configured — wire it in runner_assembly.py")
            preflight = await self._prepare_turn_callable(
                request=request,
                deps=deps,
                static_result=static_result,
                attachment_guidance=attachment_guidance,
                message_set=message_set,
                model=model,
                turn_state=turn_state,
            )
            compaction_policy = _build_compaction_policy(
                deps=deps,
                history_budget=static_result.history_budget,
                task_profile=preflight.task_profile,
            )
            # v4.0 predictive compaction: record history_tokens snapshot.
            # Cleared on successful compaction; otherwise accumulates for
            # growth-rate estimation.
            try:
                compression_runtime.recent_history_tokens.append(
                    preflight.history_tokens
                )
                # Keep only the last 20 snapshots to bound memory.
                if len(compression_runtime.recent_history_tokens) > 20:
                    compression_runtime.recent_history_tokens = (
                        compression_runtime.recent_history_tokens[-20:]
                    )
            except Exception:
                pass
            _budget_cfg = deps.config.context.budget
            _mode = getattr(_budget_cfg, "mode", "absolute")
            _input_budget: int | None = None
            if _mode == "ratio":
                _image_tokens = 0
                try:
                    _tpr = int(
                        getattr(getattr(deps.config, "vision", None), "tokens_per_image", 0)
                        or 0
                    )
                    _cap = int(
                        getattr(getattr(deps.config, "vision", None), "max_images_inline", 0)
                        or 0
                    )
                    _img_n = count_image_parts(
                        preflight.packed_request.messages if preflight.packed_request else []
                    )
                    _image_tokens = min(_img_n, _cap) * _tpr
                except Exception:
                    _image_tokens = 0
                _tools_wire = 0
                try:
                    _tools_wire = int(deps.tools.wire_tokens_estimate()) if getattr(deps, "tools", None) else 0
                except Exception:
                    _tools_wire = 0
                # ratio 为显式启用：窗口低于分区地板等配置错误在此响亮抛出
                # （ValueError），不静默退化为「压缩永不触发」的 fail-open。
                _zones = resolve_runtime_zones(
                    budget=_budget_cfg,
                    window=getattr(_budget_cfg, "window_tokens", None),
                    model_max_output=model_max_output,
                    tools_wire=_tools_wire,
                    image_tokens=_image_tokens,
                )
                _input_budget = _zones.input_budget
                # 降级阶梯（G2）：每轮计算一次降级决策并结构化记录。
                # 如实说明：渲染层尚未供给块级 actuals，D1-D9 当前不产生
                # 步骤（total_dropped 恒 0，下方 input_budget 收紧随之处眠）；
                # 实际生效的是 D10（总压力 ≥0.9 记压缩触发）与 D11（仍超窗
                # 时报 context_window_exceeded 或记 force_compaction）。
                if _zones is not None:
                    try:
                        _system_actual = 0
                        _dynamic_actual = 0
                        _pkr = getattr(preflight, "packed_request", None)
                        if _pkr is not None:
                            _sys = getattr(_pkr, "system", None)
                            if isinstance(_sys, str):
                                _system_actual = int(estimate_tokens(_sys))
                            elif isinstance(_sys, list):
                                _parts: list[str] = []
                                for _b in _sys:
                                    _t = (
                                        _b
                                        if isinstance(_b, str)
                                        else (
                                            getattr(_b, "content", None)
                                            or getattr(_b, "text", None)
                                            or ""
                                        )
                                    )
                                    if isinstance(_t, str):
                                        _parts.append(_t)
                                _system_actual = int(estimate_tokens("".join(_parts)))
                            _dynamic_actual = int(
                                getattr(_pkr, "context_payload_tokens", 0) or 0
                            )
                        _tp = getattr(preflight, "task_profile", None)
                        _is_code = bool(
                            _tp is not None
                            and getattr(_tp, "has_material_type", None) is not None
                            and _tp.has_material_type("code")
                        )
                        _compact_disabled = (
                            getattr(compression_runtime, "consecutive_failures", 0)
                            >= self._circuit_breaker_threshold
                        )
                        degradation = degrade_context(
                            zones=_zones,
                            mode="ratio",
                            is_code_task=_is_code,
                            system_actual=_system_actual,
                            dynamic_actual=_dynamic_actual,
                            compaction_disabled=_compact_disabled,
                            consecutive_failures=int(
                                getattr(compression_runtime, "consecutive_failures", 0)
                            ),
                        )
                        log_degradation_steps(logger_=logger, plan=degradation)
                        if degradation.total_dropped_tokens > 0:
                            _input_budget = max(
                                0,
                                int(_input_budget or 0) - degradation.total_dropped_tokens,
                            )
                        if degradation.error_code:
                            logger.warning(
                                "context_window_exceeded mode=ratio error_code=%s "
                                "dropped_tokens=%d",
                                degradation.error_code,
                                degradation.total_dropped_tokens,
                            )
                    except Exception as _deg_exc:
                        logger.debug("degrade_context failed: %s", _deg_exc)
            history_assessment = BudgetOrchestrator().assess_history_compaction(
                history_tokens=preflight.history_tokens,
                history_budget=static_result.history_budget,
                compaction_policy=compaction_policy,
                last_prompt_tokens=getattr(compression_runtime, "last_prompt_tokens", None),
                mode=_mode,
                input_budget=_input_budget,
            )
            should_compact = history_assessment.should_compact
            logger.info(
                "history compaction assess: history_tokens=%d total_input=%d trigger=%d should_compact=%s",
                preflight.history_tokens,
                history_assessment.total_input_tokens,
                history_assessment.trigger_budget,
                should_compact,
            )
            if not should_compact:
                # v4.0 predictive compaction: estimate if trigger budget
                # will be reached within the configured horizon.
                try:
                    # pydantic schema 保证字段齐全，直接访问（best-effort
                    # 语义仍由外层 try/except 保持：schema 树异常时跳过
                    # predictive 而非用默认值继续）。
                    history_cfg = deps.config.context.compaction.history
                    horizon = int(history_cfg.predictive_horizon)
                    min_samples = int(history_cfg.predictive_min_samples)
                    min_interval = int(history_cfg.min_interval_entries)
                    if predict_compaction_trigger(
                        runtime=compression_runtime,
                        current_tokens=preflight.history_tokens,
                        trigger_budget=history_assessment.trigger_budget,
                        current_entry_seq=len(getattr(tree, "entries", {}) or {}),
                        horizon=horizon,
                        min_samples=min_samples,
                        min_interval_entries=max(0, int(min_interval or 0)),
                        emergency_ratio=0.90,
                        history_budget=static_result.history_budget,
                    ):
                        should_compact = True
                        logger.info(
                            "predictive compaction triggered: current=%d trigger=%d",
                            preflight.history_tokens,
                            history_assessment.trigger_budget,
                        )
                except Exception:
                    pass  # Predictive is best-effort; never block the normal path
                if not should_compact:
                    self._on_recovery(compression_runtime)
                    return CompressionPreparation(
                        action="proceed",
                        execution_mode="may_defer",
                        preflight=preflight,
                        history_assessment=history_assessment,
                        compaction_policy=compaction_policy,
                        section_retention=SectionRetention.active_from_preflight(),
                    )
            if compression_runtime.consecutive_failures >= self._circuit_breaker_threshold:
                outcome_label = "circuit_open"
                logger.warning("AutoCompact circuit breaker tripped. Skipping compaction.")
                emit(
                    _METRIC_CB_TRIPPED,
                    1.0,
                    kind="counter",
                )
                return CompressionPreparation(
                    action="proceed",
                    execution_mode="may_defer",
                    preflight=preflight,
                    history_assessment=history_assessment,
                    compaction_policy=compaction_policy,
                    section_retention=SectionRetention.active_from_preflight(),
                )
            outcome = await self._maybe_pretrim_callable(
                deps=deps,
                tree=tree,
                request=request,
                query_loop_state=query_loop_state,
                compression_runtime=compression_runtime,
                history_budget=static_result.history_budget,
                history_tokens=preflight.history_tokens,
                abort_event=abort_event,
                compaction_policy=compaction_policy,
                history_assessment=history_assessment,
            )
            if outcome is None:
                outcome_label = "noop"
                execution_mode = "must_block"
                self._on_recovery(compression_runtime)
                return CompressionPreparation(
                    action="proceed",
                    execution_mode=execution_mode,
                    preflight=preflight,
                    history_assessment=history_assessment,
                    compaction_policy=compaction_policy,
                    section_retention=SectionRetention.active_from_preflight(),
                )
            if outcome.ok:
                self._on_recovery(compression_runtime)
                execution_mode = "must_block"
                if outcome.compacted:
                    outcome_label = "restart_turn"
                    # v4 §5.4 触发点 (a)：历史压缩事件边界执行一次批量 microcompact。
                    _microcompact_at_compaction_boundary(
                        query_loop_state, compression_runtime
                    )
                    # v4.0: Clear predictive compaction snapshots after
                    # successful compaction — growth rate resets.
                    try:
                        compression_runtime.recent_history_tokens = []
                    except Exception:
                        pass
                    return CompressionPreparation(
                        action="restart_turn",
                        execution_mode=execution_mode,
                        preflight=None,
                        history_assessment=history_assessment,
                        compaction_policy=compaction_policy,
                        section_retention=SectionRetention.durable_static(),
                    )
                return CompressionPreparation(
                    action="proceed",
                    execution_mode=execution_mode,
                    preflight=preflight,
                    history_assessment=history_assessment,
                    compaction_policy=compaction_policy,
                    section_retention=SectionRetention.active_from_preflight(),
                )
            outcome_label = "failure"
            execution_mode = "must_block"
            self._on_failure(compression_runtime)
            logger.warning(
                "AutoCompact failed: %s. Consecutive failures: %d",
                outcome.error_message or "compaction failed",
                compression_runtime.consecutive_failures,
            )
            return CompressionPreparation(
                action="proceed",
                execution_mode=execution_mode,
                preflight=preflight,
                history_assessment=history_assessment,
                compaction_policy=compaction_policy,
                section_retention=SectionRetention.active_from_preflight(),
                compression_error_code=getattr(outcome, "error_code", None),
                compression_error_message=getattr(outcome, "error_message", None),
            )
        finally:
            _HOTPATH.observe(
                "compression.prepare_ms",
                (time.monotonic() - started_at) * 1000.0,
                labels={
                    "outcome": outcome_label,
                    "mode": execution_mode,
                    "should_compact": should_compact,
                },
            )


def _build_compaction_policy(
    *,
    deps: Any,
    history_budget: int,
    task_profile: Any,
) -> TaskCompactionPolicy | None:
    if task_profile is None:
        return None
    return deps.task_runtime_policy.compaction_policy(
        task_profile,
        history_budget=history_budget,
        history_threshold=float(deps.context_compaction_settings.compaction_threshold),
        keep_recent_tokens=int(deps.context_compaction_settings.keep_recent_tokens),
    )


def _microcompact_at_compaction_boundary(
    query_loop_state: "QueryLoopState",
    compression_runtime: "CompressionRuntimeState",
) -> None:
    """历史压缩事件边界：batch 模式下复位 R 区批量清理计数（v4 §5.4 触发点 a）。

    压缩本身已从树重建历史（旧工具结果随被压缩段落一并移除），投影清理
    在此是冗余的；真正要做的是复位计数器，并把探针快照设为当前全量
    结果字符数——否则下一轮 tick 会把全量误判为「新增」而立即误触发
    一次清理。incremental 模式下为 no-op（每轮已滚动清理）。
    """
    try:
        from prismclaw.engine.context import microcompact as _mc

        if _mc.resolve_microcompact_mode() != "batch":
            return
        message_set = query_loop_state.query_view.messages_for_query
        _total = _mc.tool_result_chars(list(getattr(message_set, "messages", [])))
        compression_runtime.tool_result_chars_since_cleanup = 0
        compression_runtime.tool_result_chars_probe = _total
    except Exception:
        pass
