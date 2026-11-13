"""terminal — 终态收敛 + 持久化 + HITL + steering。

由 ``build_loop_config`` 绑定到 ExecutePort 的插槽：
- ``terminate``：幂等终态副作用（清计数、mark_blocked、快照、设 terminal_error）
- ``persist_turn``：合并 denied / clarified 两种落树
- ``check_hitl`` / ``wait_for_clarification``：HITL 检测与等待
- ``get_steering_messages``：steering 消费与落树

所有函数以 ``rt: LoopRuntime`` 为第一参数，显式读写受控字段。
"""
from __future__ import annotations

import dataclasses
import logging
import time as _time
from typing import Any

from prismclaw.engine.execution.control.kernel import TerminalReason
from prismclaw.engine.execution.loop._loop_helpers import (
    _build_clarify_request,
    _close_orphaned_tool_calls,
    _persist_blocked_tool_results,
    _persist_clarified_turn,
)
from prismclaw.engine.execution.loop.runtime import LoopRuntime
from prismclaw.engine.execution.loop.types import (
    HITLCheckResult,
    RunLoopState,
)
from prismclaw.schema.content.streaming import ErrorEvent

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# terminate — 终态副作用的唯一实现
# ---------------------------------------------------------------------------

async def terminate(
    rt: LoopRuntime,
    state: RunLoopState,
    code: str,
    *,
    message: str | None = None,
    terminated_by: TerminalReason | None = None,
) -> None:
    """终态副作用的唯一实现：清计数、标记阻塞、持久化快照、产出错误事件。

    幂等：``state.terminal_error`` 已设置时直接返回，不重复副作用。

    ``code`` 是 ErrorEvent 的错误码；``terminated_by`` 是遥测用的终态标签，
    缺省与 ``code`` 相同。
    """
    from prismclaw.engine.execution.loop.arbiter import mark_task_blocked, persist_snapshot_fn

    if state.terminal_error is not None:
        return

    if rt.tool_loop_state is not None:
        # run 终止时必须把所有 run 级状态清干净——下次 run 从 checkpoint
        # 恢复时 tool_loop_state 可能残留（resume 场景），跨 run 污染不可接受。
        rt.tool_loop_state.compaction_failures = 0
        rt.tool_loop_state.recent_tool_signatures.clear()
        rt.tool_loop_state.consecutive_external_errors = 0
        rt.tool_loop_state.consecutive_external_same_signature = 0
        rt.tool_loop_state.last_external_signature = ""
        rt.tool_loop_state.frozen_tools.clear()
        rt.tool_loop_state.frozen_call_signatures.clear()
        rt.tool_loop_state.tool_attempt_events.clear()
        rt.tool_loop_state.frozen_call_defied = False
        rt.tool_loop_state.convergence_advisory_sent = False
        rt.state_machine.update_tool_loop_state(rt.tool_loop_state)

    if state.ctx is not None:
        # 闭集收敛：terminated_by 显式给出时优先；否则 code 必须本身就是
        # 合法的 TerminalReason（内核/治理的终态码），否则在此刻抛错，
        # 杜绝新的魔法字符串渗入遥测通道。
        state.ctx.terminated_by = terminated_by or TerminalReason(code)
        if code != "aborted":
            await mark_task_blocked(state.ctx, code)
        await persist_snapshot_fn(state.ctx)

    state.terminal_error = ErrorEvent(
        error_code=code,
        message=message or rt.terminal_messages.get(code, "任务终止"),
    )


# ---------------------------------------------------------------------------
# persist_turn — 合并 denied / clarified 两种落树
# ---------------------------------------------------------------------------

async def persist_turn(
    rt: LoopRuntime,
    response: Any,
    kind: str,
    **kwargs: Any,
) -> None:
    """落树回合（denied 或 clarified）。

    ``kind="denied"``：全拒回合（assistant + 被拒结果）。
    ``kind="clarified"``：HITL 回合（assistant + ask 结果 + 跳过结果 + answer），
    需传 ``allowed_calls`` / ``ask_call_id`` / ``answer``。

    失败不中断骨架（尽力而为），但必须闭合配对：assistant 已落树而结果写入
    失败时，用 ``_close_orphaned_tool_calls`` 闭合孤立 tool_calls。
    """
    from prismclaw.engine.execution.dispatch.response_handling import (
        append_assistant_response,
    )
    from prismclaw.engine.execution.dispatch.turn_utils import strip_thinking
    from prismclaw.engine.execution.loop.tree_writer import _append

    try:
        await append_assistant_response(
            deps=rt.deps,
            tree=rt.tree,
            append_entry=_append,
            text=strip_thinking(response.text or ""),
            tool_calls=response.tool_calls,
        )

        if kind == "denied":
            blocked_events = kwargs.get("blocked_events", [])
            if blocked_events:
                await _persist_blocked_tool_results(
                    deps=rt.deps,
                    tree=rt.tree,
                    blocked_events=blocked_events,
                )
        elif kind == "clarified":
            await _persist_clarified_turn(
                deps=rt.deps,
                tree=rt.tree,
                response=response,
                allowed_calls=kwargs.get("allowed_calls", []),
                ask_call_id=kwargs.get("ask_call_id", ""),
                answer=kwargs.get("answer"),
            )
        else:
            raise ValueError(f"persist_turn: unknown kind={kind!r}")
    except Exception:
        logger.warning("persist_turn(%s) failed", kind, exc_info=True)
        await _close_orphaned_tool_calls(
            deps=rt.deps,
            tree=rt.tree,
            tool_calls=response.tool_calls or [],
        )


# ---------------------------------------------------------------------------
# HITL — check + wait（共享 _hitl_impl，保留两个端口薄 wrapper）
# ---------------------------------------------------------------------------

def check_hitl(
    rt: LoopRuntime,
    calls: list[Any],
) -> HITLCheckResult | None:
    """HITL 检测。AskUserQuestion 需要跨 yield 边界处理。

    契约：AskUserQuestion 若对模型可见（可被调用），则运行时依赖
    ``deps.session_queue`` 必须存在。二者不一致属配置错误，必须在此
    fail fast，不允许静默降级为「当普通工具执行」。
    """
    from prismclaw.engine.toolkit.builtin.tool_helpers import _is_ask_user_question

    ask_user_calls = [c for c in calls if _is_ask_user_question(c)]
    if not ask_user_calls:
        return None
    if not hasattr(rt.deps, "session_queue") or rt.deps.session_queue is None:
        raise RuntimeError(
            "AskUserQuestion 被调用，但 deps.session_queue 为 None："
            "该交互式工具在没有澄清通道时仍对模型可见。"
            "只要 session_queue 不可用，就必须隐藏 AskUserQuestion"
            "（HeadlessModeReducer）。"
        )
    ask_call = ask_user_calls[0]
    return HITLCheckResult(
        needs_clarification=True,
        clarification_request=_build_clarify_request(ask_call, rt.tool_ctx, rt.deps, rt.request),
    )


async def wait_for_clarification(
    rt: LoopRuntime,
    hitl: HITLCheckResult,
) -> str | None:
    """等待用户回答（纯等待不落树）。

    **无限等待**：执行在此暂停，只有用户提交作答才恢复。返回 None 表示
    非用户作答（abort / stale / 会话结束），骨架据此终止本轮。

    **不落树 answer**：落树由 ``persist_turn(kind="clarified")`` 统一负责
    （assistant → tool 结果 → user answer 的唯一正确序）。

    **等待时间不计入运行预算**：用户在想问题 / 打字回答的时间不是引擎
    消耗的算力。返回时扣除等待时间——把 ``run_started``、``soft_at``、
    ``hard_at``、``budget.run_started`` 全部往后推同样的值，让后续所有
    deadline 检查（stream_llm、pipeline、BudgetGuard、evaluate_loop_control）
    自动读到正确的剩余时间。
    """
    rt.deps.session_queue.set_phase("awaiting_clarification")
    _wait_id = (
        hitl.clarification_request.approval_id
        if hasattr(hitl.clarification_request, "approval_id")
        else ""
    )
    logger.warning(
        "hitl.wait.start approval_id=%s (无限等待，仅用户作答恢复)", _wait_id,
    )
    _wait_t0 = _time.monotonic()
    try:
        answer = await rt.deps.session_queue.wait_for_clarification(_wait_id)
    finally:
        rt.deps.session_queue.set_phase(None)
    _elapsed = _time.monotonic() - _wait_t0

    # ── 把等待时间从运行预算中扣除 ──
    _deduct_wait_time_from_budget(rt, _elapsed)

    logger.warning(
        "hitl.wait.done approval_id=%s elapsed=%.1fs answered=%s "
        "run_started_deduced=%.1fs new_hard_at=%.1f",
        _wait_id, _elapsed, answer is not None,
        _elapsed,
        _read_attr(rt, "run_state", "loop_state", "hard_at") or 0.0,
    )
    return answer


def _deduct_wait_time_from_budget(rt: LoopRuntime, elapsed_s: float) -> None:
    """把澄清等待时间从运行预算中扣除（推所有 deadline 源头字段）。

    影响的字段（都是源头，下游每次读都会重新计算剩余时间）：
    - rt.run_started（stream_llm 每轮读）
    - rt.run_state.loop_state.soft_at / hard_at（evaluate_loop_control、BudgetGuard）
    - rt.budget_guard._budget.run_started（BudgetGuard.check 里的硬墙钟检查）

    防御：字段未初始化（0 或 None）时跳过对应项，不改变原值。
    """
    if elapsed_s <= 0.0:
        return

    # LoopRuntime.run_started
    if getattr(rt, "run_started", 0.0) > 0.0:
        rt.run_started += elapsed_s

    # RunState.loop_state.soft_at / hard_at
    _loop_state = getattr(getattr(rt, "run_state", None), "loop_state", None)
    if _loop_state is not None:
        if getattr(_loop_state, "soft_at", 0.0) > 0.0:
            _loop_state.soft_at += elapsed_s
        if getattr(_loop_state, "hard_at", 0.0) > 0.0:
            _loop_state.hard_at += elapsed_s

    # BudgetGuard._budget.run_started
    # Budget 是 frozen dataclass，不能原地改字段——用 dataclasses.replace
    # 生成新实例赋回 BudgetGuard._budget。
    _bg = getattr(rt, "budget_guard", None)
    if _bg is not None and hasattr(_bg, "_budget"):
        _budget = _bg._budget
        if getattr(_budget, "run_started", 0.0) > 0.0:
            _bg._budget = dataclasses.replace(
                _budget, run_started=_budget.run_started + elapsed_s
            )


def _read_attr(obj: Any, *path: str) -> Any:
    """链式 getattr，中间任一环节 None 时返回 None。"""
    cur = obj
    for p in path:
        if cur is None:
            return None
        cur = getattr(cur, p, None)
    return cur


# ---------------------------------------------------------------------------
# steering — 消费 + 落树
# ---------------------------------------------------------------------------

async def get_steering_messages(
    rt: LoopRuntime,
) -> list[str]:
    """steering 消费：drain 会话队列 + 落树原样 user 消息。

    返回注入文本列表（空列表 = 无 steering）。落树走 HITL 同款路径，
    prepare_next_turn 树重建时带入 LLM 上下文；不进 pending_messages。
    run 结束时未消费的 steering 由 chat_runner 的 settle_steering_after_run
    兜底转回排队。非 web channel 恒返回空。

    用户方向调整 = 新进展信号：清 frozen_call_defied 一次性嫌疑。
    """
    cid = rt.request.conversation_id
    if cid is None or rt.deps.session_queue is None:
        return []
    items = rt.deps.session_queue.drain_steering(cid)
    if not items:
        return []

    from prismclaw.engine.execution.loop.tree_writer import _append
    from prismclaw.schema.agent.session import MessageEntry
    from prismclaw.schema.session.types import generate_entry_id, now_iso

    texts: list[str] = []
    for msg, _handler in items:
        content = msg.content or ""
        if not content:
            continue
        await _append(
            rt.deps,
            rt.tree,
            MessageEntry(
                id=generate_entry_id(set(rt.tree.entries.keys())),
                parent_id=rt.tree.leaf_id,
                timestamp=now_iso(),
                role="user",
                content=content,
            ),
        )
        texts.append(content)

    if texts and rt.tool_loop_state is not None and rt.tool_loop_state.frozen_call_defied:
        rt.tool_loop_state.frozen_call_defied = False
        rt.state_machine.update_tool_loop_state(rt.tool_loop_state)
    return texts
