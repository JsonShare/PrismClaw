from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Literal

from prismclaw.engine.state.tool_loop_state import ToolLoopState
from prismclaw.engine.toolkit.registry.registry import ToolRegistry
from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext
from prismclaw.schema.content.streaming import ToolCallStart

logger = logging.getLogger(__name__)

# 机械层只产 proceed/warn/freeze，不产终止——run 级终态唯一出口是
# CompletionKernel（见 §5/§6：工具循环只给引导）。
ToolLoopAction = Literal["proceed", "warn", "freeze"]

# How many recent tool-call observations are retained on ToolLoopState.
# Doom-loop detection (Check 2) reads the tail of that list, so a
# consecutive-repeat streak can never be observed as longer than this window.
# 12 > default absolute_max (2*3=6) so the freeze stage and the escalate
# counter keep headroom inside the same window.
_SIGNATURE_HISTORY_LIMIT = 12


@dataclass
class ParsedToolCall:
    call: dict[str, Any]
    name: str
    arguments: dict[str, Any]

    @property
    def call_id(self) -> str:
        """Tool call ID from the raw call dict (e.g. OpenAI ``id`` field)."""
        return self.call.get("id", "")

    @property
    def tool_name(self) -> str:
        """Alias for ``name`` — used by safety gate / audit code."""
        return self.name


@dataclass
class ToolLoopOutcome:
    action: ToolLoopAction
    state: ToolLoopState
    guidance: str | None = None
    error_message: str | None = None
    # action == "freeze" 时携带被冻结的调用签名（不含结果指纹）。
    # 调用方据此写入 ``ToolLoopState.frozen_call_signatures``（初始 0）。
    frozen_signature: str | None = None


def doom_loop_thresholds(config: Any) -> tuple[int, int]:
    """从 stuck.signature_warn 推导 doom-loop 阶梯的两级阈值。

    Returns:
        (escalate_above, absolute_max)：
        - escalate_above — 严格超过即注入结构化纠正（= signature_warn）
        - absolute_max   — 达到即 freeze（2 × signature_warn，钳制在
          历史窗口内，避免大 warn 下 freeze 永不可达）

    None 或缺字段的 config 回落到默认值 3。显式 0 保留原值（Check 2
    入口以 warn_at > 0 短路禁用整条阶梯，0 不会从这里逃逸成 3）。
    """
    stuck = getattr(config, "stuck", None)
    warn_at_raw = getattr(stuck, "signature_warn", None) if stuck is not None else None
    warn_at = 3 if warn_at_raw is None else int(warn_at_raw)
    escalate_above = warn_at
    absolute_max = min(2 * warn_at, _SIGNATURE_HISTORY_LIMIT)
    return escalate_above, absolute_max


def parse_tool_calls(tool_calls: list[dict[str, Any]]) -> list[ParsedToolCall]:
    """Parse raw tool call dicts into ParsedToolCall using the ONE true parser.

    IMPORTANT: argument parsing is delegated to ``_function_args`` in
    ``execution_entry.py``, which is the **only** code path authorised to
    interpret the ``function.arguments`` field (parse → repair → {}).
    """
    from prismclaw.engine.execution.dispatch.execution_entry import _function_args

    parsed_calls: list[ParsedToolCall] = []
    for call in tool_calls:
        fn = (call or {}).get("function") or {}
        name = fn.get("name", "") or ""
        # Single source of truth: _function_args always returns a dict —
        # either the parsed argument dict, or ``{}`` when the payload is
        # unrecoverable (repair_json_args already ran inside it).
        arguments = _function_args({"function": fn})
        parsed_calls.append(
            ParsedToolCall(
                call=call,
                name=name,
                arguments=arguments,
            )
        )
    return parsed_calls


def tool_call_start_events(
    parsed_calls: list[ParsedToolCall],
    current_step_id: str | None = None,
) -> list[ToolCallStart]:
    return [
        ToolCallStart(
            tool_call_id=parsed.call.get("id", ""),
            name=parsed.name,
            arguments=parsed.arguments,
            step_id=current_step_id,
        )
        for parsed in parsed_calls
    ]


def record_tool_call_observation(
    state: ToolLoopState,
    *,
    tool_name: str | None,
    tool_signature: str | None,
    result_fingerprint: str | None,
    is_error: bool = False,
    error_origin: str | None = None,
) -> None:
    """唯一写入点：记录一次工具调用观察（每回合恰好一次）。

    由 agent_loop 在 execute_tools 之后调用。写入：
    - 配对 token ``"{sig}@{fp}"`` append 到 ``recent_tool_signatures``
      （窗口裁剪到 ``_SIGNATURE_HISTORY_LIMIT``）
    - external 失败分流计数（``consecutive_external_errors`` 等）

    旧实现把 mutation 混在 evaluate_tool_loop_policies 里，且该函数
    每回合被 preflight/post 各调一次 → 同一签名每回合被记两次，
    名义阈值实际是 1/2。拆分后 evaluate 只读，本函数是唯一计数源。
    """
    # 失败归因分流：external 失败
    # （外部数据源/网络，见 ToolResult.error_origin）不产生任何模型
    # 空转信号——不进签名窗口（Check 2 无信号）。只累计 external 计数
    # 供判定函数区分「换源对抗中」与「死磕单一挂掉源」。
    _is_external_failure = bool(is_error and error_origin == "external")
    if _is_external_failure:
        state.consecutive_external_errors += 1
        if tool_signature and tool_signature == state.last_external_signature:
            state.consecutive_external_same_signature += 1
        else:
            state.consecutive_external_same_signature = 1
            state.last_external_signature = tool_signature or ""
    else:
        state.consecutive_external_errors = 0
        state.consecutive_external_same_signature = 0
        state.last_external_signature = ""

    if tool_signature and not _is_external_failure:
        token = tool_signature
        if result_fingerprint:
            token = f"{tool_signature}@{result_fingerprint}"
        state.recent_tool_signatures.append(token)
        if len(state.recent_tool_signatures) > _SIGNATURE_HISTORY_LIMIT:
            state.recent_tool_signatures = state.recent_tool_signatures[-_SIGNATURE_HISTORY_LIMIT:]


def evaluate_tool_loop_policies(
    *,
    state: ToolLoopState,
    config: Any = None,
) -> ToolLoopOutcome:
    """评估工具循环策略并返回动作决策。

    只读：所有计数 mutation 在 ``record_tool_call_observation`` 中完成。
    调用方必须先 record 再 evaluate（同一回合一次）。

    仅保留 Check 2（doom-loop 签名冻结）：
      streak == warn_at               → soft warn
      warn_at < streak < absolute_max → 结构化纠正注入（warn）
      streak >= absolute_max          → FREEZE 签名

    其余循环信号（per-tool 用量、error-fatigue、search-loop、同目标重读）
    已由全局 ``tool_calls`` 预算 + wrapping/last_turn 冻结覆盖，删除以
    收敛阈值面。
    """
    # Import here to avoid circular imports
    from prismclaw.schema.config import RunLoopTuningConfig

    # Default config if not provided
    if config is None:
        config = RunLoopTuningConfig()

    # Check 2: Repeated tool signature detection — staged doom-loop cap.
    #
    # ``streak`` 是 ``recent_tool_signatures`` 尾部的 run-length。
    # 元素是配对 token "{sig}@{result_fingerprint}"，streak 只在
    # "同调用 + 逐字节同结果" 时增长——真正的 doom-loop 信号。
    stuck = getattr(config, "stuck", None)
    warn_at = getattr(stuck, "signature_warn", 3) if stuck is not None else 3
    if warn_at > 0 and state.recent_tool_signatures:
        latest_token = state.recent_tool_signatures[-1]
        streak = 0
        for sig in reversed(state.recent_tool_signatures):
            if sig != latest_token:
                break
            streak += 1

        escalate_above, absolute_max = doom_loop_thresholds(config)

        if streak >= absolute_max:
            frozen_sig = latest_token.split("@", 1)[0]
            logger.warning(
                "loop_policy check_2: doom-loop FREEZE. "
                "signature=%s streak=%d escalate_above=%d absolute_max=%d",
                latest_token, streak, escalate_above, absolute_max,
            )
            return ToolLoopOutcome(
                action="freeze",
                state=state,
                frozen_signature=frozen_sig,
                guidance=(
                    f"检测到 doom-loop：同一工具调用（本次结果与之前逐字节相同）已连续重复 "
                    f"{streak} 次，且没有任何新进展。\n"
                    f"该调用已被冻结：再次发起完全相同的调用会被直接阻止，不会执行。\n"
                    f"必须从以下两条中选择一条立即执行（不要停下来解释或请求确认）：\n"
                    f"1. 用已经拿到的结果直接完成用户任务并交付，不再调用该工具；\n"
                    f"2. 换一个工具，或显著改变参数（不同的路径 / 查询词 / 范围），"
                    f"仅做无意义的微调不算改变。\n"
                    f"禁止：不要向用户解释你为什么卡住、不要写状态报告、不要请求用户确认、"
                    f"不要展示内部推理或策略调整过程。直接推进并交付结果。\n"
                    f"若在冻结后仍反复尝试该调用且始终不改变策略，本次运行将无法通过完成受理。"
                ),
            )

        if streak > escalate_above:
            remaining = absolute_max - streak
            logger.warning(
                "loop_policy check_2: doom-loop CORRECTION injected. "
                "signature=%s streak=%d escalate_above=%d absolute_max=%d",
                latest_token, streak, escalate_above, absolute_max,
            )
            return ToolLoopOutcome(
                action="warn",
                state=state,
                guidance=(
                    f"检测到 doom-loop：同一工具调用（本次结果与之前逐字节相同）已连续重复 "
                    f"{streak} 次，超过硬上限 {escalate_above} 次，且没有取得任何新进展。\n"
                    f"立即停止重复这个调用。必须从以下两条中选择一条立即执行（不要停下来解释或请求确认）：\n"
                    f"1. 用已经拿到的结果直接完成用户任务并交付，不再调用该工具；\n"
                    f"2. 换一个工具，或显著改变参数（不同的路径 / 查询词 / 范围），"
                    f"仅做无意义的微调不算改变。\n"
                    f"禁止：不要向用户解释你为什么卡住、不要写状态报告、不要请求用户确认、"
                    f"不要展示内部推理或策略调整过程。直接推进并交付结果。\n"
                    f"再重复 {remaining} 次相同调用将冻结该调用。"
                ),
            )

        if streak >= warn_at:
            return ToolLoopOutcome(
                action="warn",
                state=state,
                guidance=f"工具调用签名已重复 {warn_at} 次。请考虑换一种做法。",
            )

    # All checks passed
    return ToolLoopOutcome(action="proceed", state=state)


def check_frozen_call(
    *,
    state: ToolLoopState,
    tool_signature: str,
    config: Any = None,
    counted_this_turn: set[str] | None = None,
) -> ToolLoopOutcome:
    """before_tool_call 冻结检查：命中冻结表即 block，反复尝试则升级 terminate。

    Check 2 判定 freeze 后，签名进入 ``frozen_call_signatures``（初始 0）。
    本函数在下一回合 before_tool_call 阶段被调用：

    - 签名未冻结 → ``proceed``
    - 签名已冻结 → block（调用方以被冻结为由 block 该调用，模型收到
      结构化冻结告知）；计数 +1 并返回 ``freeze``。计数达到
      ``escalate_above`` 时置 ``state.frozen_call_defied`` 嫌疑标志
      并返回 ``freeze``——模型已连续无视冻结（只置嫌疑标志，不产生终态）。

    ``counted_this_turn``：本回合已计数的签名集合（由调用方跨本回合
    的多次 before_tool_call 传递）。同一签名一回合只 +1——模型并行
    发 N 个相同调用不耗 N 次配额，escalate 保持"跨回合 N 次无视"
    语义。None 视为空集（每次调用独立计数）。

    冻结失效无需显式清除：模型改变调用参数 → 新签名不在冻结表中；
    结果变化 → 配对 token 变 → streak 归 1，不再产生新的 freeze。
    """
    if not tool_signature:
        return ToolLoopOutcome(action="proceed", state=state)

    if tool_signature not in state.frozen_call_signatures:
        return ToolLoopOutcome(action="proceed", state=state)

    counted = counted_this_turn if counted_this_turn is not None else set()
    if tool_signature in counted:
        # 本回合已计过数：仍 block，但不重复累加
        attempts = state.frozen_call_signatures[tool_signature]
    else:
        state.frozen_call_signatures[tool_signature] = (
            state.frozen_call_signatures.get(tool_signature, 0) + 1
        )
        counted.add(tool_signature)
        attempts = state.frozen_call_signatures[tool_signature]
    escalate_above, _ = doom_loop_thresholds(config)

    if attempts >= escalate_above:
        logger.warning(
            "loop_policy frozen_call: ESCALATE to SUSPICION. "
            "signature=%s frozen_attempts=%d escalate_above=%d",
            tool_signature, attempts, escalate_above,
        )
        # 不再直接杀任务，只置嫌疑标志；终态由 CompletionKernel 裁决。
        state.frozen_call_defied = True
        return ToolLoopOutcome(
            action="freeze",
            state=state,
            guidance=(
                f"该工具调用已被 doom-loop 防护冻结，且你已连续 {attempts} 次无视冻结\n"
                f"重复尝试同一调用（升级阈值 {escalate_above}）。\n"
                f"必须从以下两条中选择一条立即执行（不要停下来解释或请求确认）：\n"
                f"1. 用已经拿到的结果直接完成用户任务并交付；\n"
                f"2. 换一个工具，或显著改变参数（不同的路径 / 查询词 / 范围），"
                f"仅做无意义的微调不算改变。\n"
                f"若始终不改变策略，本次运行将无法通过完成受理。"
            ),
        )

    logger.warning(
        "loop_policy frozen_call: BLOCKED frozen signature. "
        "signature=%s frozen_attempts=%d escalate_above=%d",
        tool_signature, attempts, escalate_above,
    )
    return ToolLoopOutcome(
        action="freeze",
        state=state,
        guidance=(
            "该工具调用已被 doom-loop 防护冻结（此前同一调用已连续重复且结果无任何变化）。\n"
            "本次调用不会执行。必须从以下两条中选择一条立即执行（不要停下来解释或请求确认）：\n"
            "1. 用已经拿到的结果直接完成用户任务并交付；\n"
            "2. 换一个工具，或显著改变参数（不同的路径 / 查询词 / 范围），"
            "仅做无意义的微调不算改变。\n"
            "若继续尝试该调用且始终不改变策略，本次运行将无法通过完成受理。"
        ),
    )


def _resolve_approval_runtime_ids(tool_ctx: ToolContext) -> tuple[str, str]:
    request_id = tool_ctx.request_id
    if not isinstance(request_id, str) or not request_id:
        raise RuntimeError("approval preflight requires tool_ctx.request_id")
    task_spec = tool_ctx.task_spec
    if task_spec is None:
        raise RuntimeError("approval preflight requires tool_ctx.task_spec")
    return request_id, f"{task_spec.task_id}:turn:{task_spec.turn_index}"


# ── P2 prepare/execute two-phase separation (aligned with Pi) ──

async def prepare_call(
    tool_call: dict[str, Any],
    context: ToolContext,
    registry: ToolRegistry | None = None,
    safety_gate: Any | None = None,
) -> Any:
    """Prepare phase — validate, safety-check, resolve tool.

    Aligned with Pi's prepareToolCall: the prepare phase determines
    whether the call should proceed, and if so, resolves the tool
    and its arguments. Interception is possible here (safety gate).

    Returns PreparedCall (ready to execute) or ImmediateResult (intercepted).
    """
    from prismclaw.engine.execution.dispatch.prepared_types import ImmediateResult, PreparedCall
    from prismclaw.schema.content.content_types import ToolResult

    tool_name = (tool_call.get("function") or {}).get("name", "")
    tool = registry.get(tool_name) if registry else None

    if tool is None:
        return ImmediateResult(
            tool_call=tool_call,
            result=ToolResult(
                tool_call_id=tool_call.get("id", ""),
                content=[f"Tool '{tool_name}' not found"],
                is_error=True,
            ),
            is_error=True,
        )

    # Safety gate interception
    if safety_gate is not None:
        try:
            decision = await safety_gate.evaluate(tool_call, tool, context)
            if getattr(decision, "blocked", False):
                return ImmediateResult(
                    tool_call=tool_call,
                    result=ToolResult(
                        tool_call_id=tool_call.get("id", ""),
                        content=[getattr(decision, "reason", "已被安全门禁阻止")],
                        is_error=True,
                    ),
                    is_error=True,
                )
            return PreparedCall(tool_call=tool_call, tool=tool,
                                    args=tool_call.get("function", {}).get("arguments", {}),
                                    safety_decision=decision)
        except Exception:
            pass  # Safety gate failure -> proceed

    args_str = (tool_call.get("function") or {}).get("arguments", "{}")
    try:
        args = json.loads(args_str) if isinstance(args_str, str) else args_str
    except (json.JSONDecodeError, TypeError):
        args = {}

    return PreparedCall(
        tool_call=tool_call,
        tool=tool,
        args=args,
    )


async def execute_prepared(
    prepared: Any,
    *,
    signal: Any | None = None,
) -> Any:
    """Execute phase — run the prepared call, no interception possible.

    Aligned with Pi's executePreparedToolCall: once prepared, the
    execution cannot be intercepted. This ensures deterministic
    tool execution after the prepare phase validates it.
    """
    from prismclaw.engine.execution.dispatch.prepared_types import PreparedCall

    if not isinstance(prepared, PreparedCall):
        raise TypeError(f"Expected PreparedCall, got {type(prepared)}")

    tool = prepared.tool
    kwargs = dict(prepared.args) if prepared.args else {}
    if signal is not None:
        kwargs["signal"] = signal

    result = await tool.execute(**kwargs)
    return result
