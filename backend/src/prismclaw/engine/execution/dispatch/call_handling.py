from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Literal

from prismclaw.engine.state.tool_loop_state import ToolLoopState
from prismclaw.engine.toolkit.registry.registry import ToolRegistry
from prismclaw.engine.toolkit.registry.tool_classification import is_search_intent_tool
from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext
from prismclaw.schema.agent.task_types import TaskSpec
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

# 有界循环（bounded cycling）判定：签名窗口已满（12 条观察）且窗口内
# 独特 "{sig}@{fp}" 组合数 <= 此值时，判定 agent 在极小的「调用+结果」
# 组合集合里打转。2 是保守上限——正常推进的任务窗口内独特组合会持续
# 增长（新调用或新结果都算新信息）；只有真死循环才会被困在 1-2 个
# 组合里。结果指纹每次都变的循环不在此列（那是新信息，墙钟兜底）。
_CYCLING_UNIQUE_CAP = 2

# Check 5 同目标重复读取：结果内容达到此字符数才计入「读取」。
# 用结果规模区分读/写，避免解析 shell 命令语义——读取返回内容
# （sed/cat/read/grep 命中），写入/小输出命令（echo > f、cp、
# wc -l、ls）不构成读取。低于阈值的小文件读取是已知漏报面
# （宁漏勿误：本检查只产 advisory，评审层仍会兜底）。
_READ_MIN_RESULT_CHARS = 500

# 目标参数名：先取指向性参数，抓不到即无唯一目标。
_TARGET_ARG_NAMES = ("file_path", "path", "file", "filepath", "url", "query")

# 命令中的路径样 token（至少含一个 /；绝对/相对皆可，前置
# lookbehind 防把版本号/URL 片段切半个）。
_PATH_TOKEN_RE = re.compile(r"(?<![\w.-])(?:/)?[\w.@+-]+(?:/[\w.@+-]+)+/?")


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
    # Check 4（search-loop freeze）携带要从可见工具集中移除的工具名。
    # 调用方据此写入 ``ToolLoopState.frozen_tools``。
    frozen_tools_to_freeze: set[str] | None = None


def doom_loop_thresholds(config: Any) -> tuple[int, int]:
    """从配置推导 doom-loop 阶梯的两级阈值。

    Returns:
        (escalate_above, absolute_max)：
        - escalate_above — 严格超过即注入结构化纠正（cap 与 warn_at 的较大者）
        - absolute_max   — 达到即 freeze（escalate_above 的 2 倍，钳制在
          历史窗口内，避免大 cap 下 freeze 永不可达）

    None 或缺字段的 config 回落到 RunLoopTuningConfig 默认值（3）。
    显式 0 保留原值（Check 2 入口以 warn_at > 0 短路禁用整条阶梯，
    0 不会从这里逃逸成 3）。
    """
    warn_at_raw = getattr(config, "repeated_tool_signature_threshold", None)
    cap_raw = getattr(config, "doom_loop_threshold", None)
    warn_at = 3 if warn_at_raw is None else int(warn_at_raw)
    cap = warn_at if cap_raw is None else int(cap_raw)
    escalate_above = max(cap, warn_at)
    absolute_max = min(2 * escalate_above, _SIGNATURE_HISTORY_LIMIT)
    return escalate_above, absolute_max


def parse_tool_calls(tool_calls: list[dict[str, Any]]) -> list[ParsedToolCall]:
    """Parse raw tool call dicts into ParsedToolCall using the ONE true parser.

    IMPORTANT: argument parsing is delegated to ``_function_args`` in
    ``execution_entry.py``, which is the **only** code path authorised to
    interpret the ``function.arguments`` field.  ``call_handling.py``
    previously had its own ``json.loads`` + ``{"_raw": …}`` fallback —
    that duplicate was the root cause of "same input, different outcome"
    bugs depending on which consumer you hit.
    """
    from prismclaw.engine.execution.dispatch.execution_entry import _function_args

    parsed_calls: list[ParsedToolCall] = []
    for call in tool_calls:
        fn = (call or {}).get("function") or {}
        name = fn.get("name", "") or ""
        # Single source of truth: _function_args guarantees two shapes only:
        #   1. A real argument dict   → tools see their expected keys
        #   2. Exactly ``{"_raw": bytes}`` → execution layer short-circuits
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
    - ``per_tool_call_counts[tool_name] += 1``
    - 配对 token ``"{sig}@{fp}"`` append 到 ``recent_tool_signatures``
      （窗口裁剪到 ``_SIGNATURE_HISTORY_LIMIT``）
    - ``consecutive_errors`` 连错计数

    旧实现把 mutation 混在 evaluate_tool_loop_policies 里，且该函数
    每回合被 preflight/post 各调一次 → 同一签名每回合被记两次，
    名义阈值实际是 1/2。拆分后 evaluate 只读，本函数是唯一计数源。
    """
    if tool_name:
        state.per_tool_call_counts[tool_name] = state.per_tool_call_counts.get(tool_name, 0) + 1

    # 失败归因分流：external 失败
    # （外部数据源/网络，见 ToolResult.error_origin）不产生任何模型
    # 空转信号——不进签名窗口（Check 2/2b 无信号）、不计连错
    # （Check 3 只管 internal），只累计 external 计数供判定函数区分
    # 「换源对抗中」与「死磕单一挂掉源」。预算计数照旧（防烧钱）。
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
        token = tool_signature if not result_fingerprint else f"{tool_signature}@{result_fingerprint}"
        state.recent_tool_signatures.append(token)
        if len(state.recent_tool_signatures) > _SIGNATURE_HISTORY_LIMIT:
            state.recent_tool_signatures = state.recent_tool_signatures[-_SIGNATURE_HISTORY_LIMIT:]
        # 有界循环计数（与配对 token 同一写入点）：窗口满且独特组合
        # 极少 → +1；出现任何新组合（新调用或新结果）→ 归 0。
        # 这是进度信号，不是迭代计数——真正在推进的任务永远不会累计。
        if (
            len(state.recent_tool_signatures) >= _SIGNATURE_HISTORY_LIMIT
            and len(set(state.recent_tool_signatures)) <= _CYCLING_UNIQUE_CAP
        ):
            state.cycling_loop_streak += 1
        else:
            state.cycling_loop_streak = 0

    if is_error and not _is_external_failure:
        state.consecutive_errors += 1
    else:
        state.consecutive_errors = 0


def tool_result_char_count(result: Any) -> int:
    """工具结果文本字符数（content blocks 的 text 长度和）。"""
    if result is None:
        return 0
    content = getattr(result, "content", None) or []
    chars = 0
    for block in content:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            chars += len(text)
    return chars


def extract_call_target(tool_name: str | None, args: dict[str, Any] | None) -> str | None:
    """提取调用的唯一目标（文件路径 / URL / 查询词）；歧义或缺失返回 None。

    - 参数形态：file_path/path/file/filepath/url/query 任一非空字符串；
    - bash 形态：从 command 提取路径样 token，恰有一个才采用
      （``cp a b`` 这类多目标调用弃权——猜错目标比不猜更糟）。
    """
    args = args or {}
    for key in _TARGET_ARG_NAMES:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    command = args.get("command")
    if isinstance(command, str) and command.strip():
        distinct = sorted(set(_PATH_TOKEN_RE.findall(command)))
        if len(distinct) == 1:
            return distinct[0]
    return None


def record_target_observations(
    state: ToolLoopState,
    tool_calls: list[Any],
    results: list[Any] | None,
    tool_registry: Any = None,
) -> None:
    """按批次记录同目标读取/写入计数（Check 5 数据源，全调用覆盖）。

    与 record_tool_call_observation 的分工：后者只观察每批次
    首个调用（签名 streak 语义），此处覆盖批次内全部调用——目标
    计数是累计信号，漏计平行调用直接削弱检测灵敏度。由
    after_tool_call 调用（先于 should_continue(post) 的 evaluate，
    计数当回合即可用）。

    读/写判定（机械，无 shell 语义解析）：
    - 读取：唯一目标 + 结果内容 >= _READ_MIN_RESULT_CHARS；
    - 写入：唯一目标 + tool_class == "write" + 小结果。bash 的
      tool_class 恒为 "write"（unknown_effect 保守默认），但其
      读取同样返回大内容不会被误判；小结果 bash 命令（cp/mv/
      echo>）记为写入，方向安全（抑制预警）。
    - 其余（无目标 / read 类小结果 / 歧义多目标）不计。
    """
    if not tool_calls:
        return
    from prismclaw.engine.toolkit.registry.tool_classification import resolve_tool_class

    results_by_id = {
        getattr(r, "tool_call_id", ""): r for r in (results or [])
    }
    for call in tool_calls:
        name = getattr(call, "name", "") or ""
        args = getattr(call, "arguments", None)
        target = extract_call_target(name, args if isinstance(args, dict) else None)
        if not target:
            continue
        call_id = getattr(call, "call_id", "")
        result = results_by_id.get(call_id) if call_id else None
        chars = tool_result_char_count(result)
        if chars >= _READ_MIN_RESULT_CHARS:
            state.target_read_counts[target] = state.target_read_counts.get(target, 0) + 1
        elif resolve_tool_class(name, tool_registry) == "write":
            state.target_write_counts[target] = state.target_write_counts.get(target, 0) + 1


def record_tool_attempt(
    state: ToolLoopState,
    tool_calls: list[Any],
    results: list[Any] | None,
    *,
    iteration: int = 0,
) -> None:
    """台账不变量（v2.2，吸收 Pi failToolCallsFromTruncatedMessage 纪律）：
    每一次工具尝试确定性落账，无论成功/失败/截断。

    - 成功（result 非空且非错误）→ outcome="ok"
    - 失败（result.is_error）→ outcome="error"，带 error_origin
    - 截断（call 存在但无配对 result / result 为 None）→ outcome="truncated"
      （伪造错误结果入账，避免悬空——对应 Pi 的纪律：每 tool_use 必配
      tool_result，截断则伪造错误结果而非留悬空）

    有界：超出 _TOOL_ATTEMPT_CAP 弹出最旧。供纠偏层识别"表面在跑、实则
    失败被掩盖"的盲区（20:58 形态之一）。
    """
    if not tool_calls:
        return
    results_by_id = {
        getattr(r, "tool_call_id", ""): r for r in (results or [])
    }
    cap = state._TOOL_ATTEMPT_CAP
    for call in tool_calls:
        name = getattr(call, "name", "") or ""
        if not name:
            continue
        call_id = getattr(call, "call_id", "")
        result = results_by_id.get(call_id) if call_id else None
        target = extract_call_target(name, getattr(call, "arguments", None))
        if result is None:
            outcome = "truncated"
            origin = None
        elif getattr(result, "is_error", False):
            outcome = "error"
            origin = getattr(result, "error_origin", None)
        else:
            outcome = "ok"
            origin = None
        state.tool_attempt_events.append({
            "iter": iteration,
            "tool": name,
            "target": target,
            "outcome": outcome,
            "error_origin": origin,
        })
        if len(state.tool_attempt_events) > cap:
            state.tool_attempt_events = state.tool_attempt_events[-cap:]


def evaluate_tool_loop_policies(
    *,
    state: ToolLoopState,
    config: Any = None,
    tool_name: str | None = None,
    tool_target: str | None = None,
) -> ToolLoopOutcome:
    """Evaluate tool loop policies and return an action decision.

    只读：所有计数 mutation 在 record_tool_call_observation / 
    record_target_observations 中完成。调用方必须先 record 再
    evaluate（同一回合一次）。

    Args:
        state: Current tool loop state.
        config: RunLoopTuningConfig with thresholds.
        tool_name: Name of the tool being called (for Check 1/4).
        tool_target: Extracted call target (for Check 5).

    Returns:
        ToolLoopOutcome with action ("proceed", "warn", or "freeze")——本层
        不产终止；终态只由 CompletionKernel 在模型停止时裁决。
    """
    # Import here to avoid circular imports
    from prismclaw.schema.config import RunLoopTuningConfig

    # Default config if not provided
    if config is None:
        config = RunLoopTuningConfig()

    # Check 1: Per-tool max uses
    if tool_name and tool_name in config.tool_max_uses:
        max_uses = config.tool_max_uses[tool_name]
        current_count = state.per_tool_call_counts.get(tool_name, 0)
        if current_count >= max_uses:
            # A search-intent tool hitting its cap should NOT terminate the
            # run — the model still has its own knowledge to answer from.
            # Downgrade to "warn" with guidance so the model falls back
            # to its own knowledge instead of hard-terminating.
            if is_search_intent_tool(tool_name, config=config):
                return ToolLoopOutcome(
                    action="warn",
                    state=state,
                    guidance=(
                        f"{tool_name} 已使用 {current_count} 次，达到单次运行上限 ({max_uses})。\n"
                        f"请不再调用 {tool_name}，使用已有搜索结果或你自身的知识来回答用户问题。"
                    ),
                )
            # 任何工具的用量上限都不应终止整个 run（进度驱动的终止只
            # 属于 doom-loop 阶梯）。统一降级为 warn：模型收到引导后
            # 换工具或基于已有结果继续，正常任务继续执行。
            return ToolLoopOutcome(
                action="warn",
                state=state,
                guidance=(
                    f"{tool_name} 已使用 {current_count} 次，达到单次运行上限 ({max_uses})。\n"
                    f"请不再调用 {tool_name}，基于已有结果继续任务，或改用其他工具。"
                ),
            )
    
    # Check 2: Repeated tool signature detection — staged doom-loop cap.
    #
    # Escalation ladder (hard cap aligned with opencode's DOOM_LOOP_THRESHOLD=3):
    #   streak == warn_at               → soft warn, legacy message unchanged.
    #   warn_at < streak < absolute_max → structured corrective instruction
    #       returned as ``guidance`` with action="warn" (injected into
    #       pending_messages by the caller; the loop continues).
    #   streak >= absolute_max          → FREEZE: the caller registers the
    #       signature in ``ToolLoopState.frozen_call_signatures`` and
    #       ``check_frozen_call`` blocks further identical calls BEFORE
    #       execution. Re-attempting the frozen call ``escalate_above``
    #       more times sets ``frozen_call_defied`` (a suspicion flag only).
    #
    # ``streak`` is the run-length of the TAIL of ``recent_tool_signatures``.
    # Elements are paired tokens "{sig}@{result_fingerprint}", so the streak
    # only grows on "same call AND byte-identical result" — the true doom-loop
    # signal. A changed result (or a different call) produces a different
    # token and the streak resets to 1 with no separate counter to drift.
    warn_at = config.repeated_tool_signature_threshold
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
                guidance=f"工具调用签名已重复 {config.repeated_tool_signature_threshold} 次。请考虑换一种做法。",
            )

    # Check 2b: Bounded-cycling detection（有界循环检测）。
    #
    # Check 2 的尾随 streak 只在"同调用且结果逐字节相同"**连续**重复时
    # 增长；若循环在 1-2 个「调用+结果」组合之间**交替**（A,B,A,B…，
    # 尾随 streak 恒为 1），冻结阶梯永远够不到——eadc0d 实测：61 次
    # 完成审查全部 repair，跑满 run_seconds（3711s）才被墙钟终止。
    #
    # 进度信号：窗口已满（12 条观察）且独特组合数 <= _CYCLING_UNIQUE_CAP，
    # 说明 agent 被困在 1-2 个调用+结果组合里、没有产生任何新信息。
    # cycling_loop_streak 由 record_tool_call_observation 维护：出现任何
    # 新组合即归 0，因此真正推进的任务永远不会触发本检查。结果指纹
    # 每次都变的循环不在此列（新信息 = 疑似正常推进，由 run_seconds 兜底）。
    # 阶梯：warn_at 时纠正引导；absolute_max 时也只产 warn——冻结单个
    # 签名对 2-组合交替无效（模型会切换到另一个组合），但终止不归本层：
    # CompletionKernel 在模型停止时统一裁决。
    cycling_streak = getattr(state, "cycling_loop_streak", 0)
    if warn_at > 0 and cycling_streak > 0:
        _cyc_escalate, _cyc_max = doom_loop_thresholds(config)
        if cycling_streak >= _cyc_max:
            logger.warning(
                "loop_policy check_2b: bounded-cycling SUSPICION. "
                "unique_tokens=%d window=%d cycling_streak=%d absolute_max=%d",
                len(set(state.recent_tool_signatures)),
                len(state.recent_tool_signatures),
                cycling_streak, _cyc_max,
            )
            return ToolLoopOutcome(
                action="warn",
                state=state,
                guidance=(
                    f"检测到有界循环：最近的工具调用始终在极小的调用/结果组合里打转，"
                    f"没有产生任何新进展（连续 {cycling_streak} 次无新组合）。\n"
                    f"必须改变策略：换一个工具、显著修改参数，或基于已有结果直接完成任务交付。\n"
                    f"若未产出任何交付物且始终不改变策略，本次运行将无法通过完成受理。"
                ),
            )
        if cycling_streak >= warn_at:
            logger.warning(
                "loop_policy check_2b: bounded-cycling CORRECTION. "
                "cycling_streak=%d warn_at=%d",
                cycling_streak, warn_at,
            )
            return ToolLoopOutcome(
                action="warn",
                state=state,
                guidance=(
                    f"检测到有界循环：最近的工具调用始终在极小的调用/结果组合里打转，"
                    f"没有产生任何新进展（连续 {cycling_streak} 次无新组合）。\n"
                    f"必须改变策略：换一个工具、显著修改参数，或基于已有结果直接完成任务交付。\n"
                    f"若继续原地打转且未产出交付物，本次运行将无法通过完成受理。"
                ),
            )

    # Check 3: Error fatigue detection
    # 连续错误是值得警示的信号，但不构成终止整个 run 的理由（工具
    # 连续失败可能只是环境抖动；模型收到引导后会换方法）。真正反复
    # 重试同一失败调用的死循环由 Check 2/2b 的签名阶梯兜底。
    # 归因净化（2026-10-24）：consecutive_errors 已只累计 internal
    # 失败（external 数据源失败在 record 侧分流），本检查天然只管
    # 模型自身错误；external 连错的降级引导另行处理。
    if config.error_fatigue_threshold > 0 and state.consecutive_errors >= config.error_fatigue_threshold:
        return ToolLoopOutcome(
            action="warn",
            state=state,
            guidance=(
                f"工具调用已连续失败 {state.consecutive_errors} 次（阈值 {config.error_fatigue_threshold}）。\n"
                f"请停止重试当前方法：检查参数是否正确、换用其他工具，"
                f"或基于已有信息继续完成任务。"
            ),
        )
    
    # Check 4: Search-loop detection (v4.0 dual-condition).
    # Signal 1: cumulative search-intent tool call count >= warn threshold.
    # Signal 2: tool output chars exceed freeze_share * search_budget_chars.
    #   search_budget_chars = sum(tool_max_uses[tool] * per_result_char_cap
    #                             for each search-intent tool).
    # Warn when signal 1 fires alone; Freeze (remove tools from visible set)
    # when both signals fire together.
    search_tools = set(getattr(config, "search_intent_tools", []) or [])
    warn_at = getattr(config, "search_loop_warn_at", 5)
    freeze_share = getattr(config, "search_freeze_output_share", 0.5)
    if warn_at > 0 and tool_name in search_tools:
        search_total = sum(
            state.per_tool_call_counts.get(n, 0)
            for n in search_tools
        )
        if search_total >= warn_at:
            # 单条结果预算的唯一来源（compaction.tool_results.per_result_char_cap）。
            # 本函数只持有 run_loop 配置，从这里读取全量 agent 配置。
            from prismclaw.infra.settings import load_settings

            default_chars = int(
                load_settings().agent.context.compaction.tool_results.per_result_char_cap
            )
            tool_max_uses = getattr(config, "tool_max_uses", None) or {}
            total_cap = sum(int(tool_max_uses.get(t, 1)) for t in search_tools)
            search_budget_chars = max(1, total_cap * default_chars)
            output_share = (
                state.tool_output_chars_used / search_budget_chars
                if search_budget_chars > 0 else 0.0
            )
            if output_share >= freeze_share:
                # Dual condition: Freeze search tools. evaluate 只读——
                # 冻结的工具名经 outcome.frozen_tools_to_freeze 携带，
                # 由调用方写入 state.frozen_tools。
                logger.warning(
                    "loop_policy check_4: search-loop FREEZE triggered. "
                    "search_total=%d output_chars=%d budget_chars=%d share=%.2f",
                    search_total, state.tool_output_chars_used,
                    search_budget_chars, output_share,
                )
                return ToolLoopOutcome(
                    action="warn",
                    state=state,
                    frozen_tools_to_freeze=set(search_tools),
                    guidance=(
                        f"搜索类工具已累计调用 {search_total} 次，且工具输出占预算比例达 "
                        f"{output_share:.0%}（阈值 {freeze_share:.0%}，预算 {search_budget_chars} 字符）。\n"
                        f"搜索工具已被冻结。请基于已有信息回答，或告知用户无法获取更多数据。"
                    ),
                )
            # Signal 1 only: Warn.
            logger.info(
                "loop_policy check_4: search-loop WARN. "
                "search_total=%d output_chars=%d budget_chars=%d share=%.2f",
                search_total, state.tool_output_chars_used,
                search_budget_chars, output_share,
            )
            return ToolLoopOutcome(
                action="warn",
                state=state,
                guidance=(
                    f"搜索类工具已累计调用 {search_total} 次 "
                    f"（工具输出 {state.tool_output_chars_used} 字符，占预算 {output_share:.0%}）。\n"
                    f"请评估是否已有足够信息回答，避免无意义的重复搜索。"
                ),
            )
    
    # Check 5: Same-target re-read detection（同目标重复读取早期预警）。
    #
    # Check 2 的指纹是「同调用 + 逐字节同结果」，对「同文件不同区间」
    # 的重读天然失效（每次区间/字节都不同 = 机械层面的新信息）。
    # 2026-10-23 s:d54adf8fb4aa4742 实测：26 轮反复 sed/cat 同一
    # mining_work 区间，L1 全过，直到 L2 评审第 3 次连击才熔断——
    # 前 12 轮零拦截。本检查把干预点从 iter 17 提前到第 K 次重读。
    #
    # 信号（仿 Check 4 双条件，但只产 advisory 不冻结——读取是任务
    # 刚需，冻结会误伤合法读取；Check 4 能冻是因为搜索可弃）：
    #   1. 当前调用目标累计读取 >= same_target_read_warn_at；
    #   2. 该目标从未被写入（读后编辑再读是合法模式，必须排除）。
    # 计数由 record_target_observations 维护，本检查只读。预警后
    # 每 3 次重读再提醒（K 精确命中 + (count-K)%3==0），无额外状态。
    # 搜索类工具归 Check 4 管（query 目标的重复同一查询在那一边
    # 计价），此处跳过避免双重预警。
    same_target_warn_at = getattr(config, "same_target_read_warn_at", 4)
    if (
        same_target_warn_at > 0
        and tool_target
        and not is_search_intent_tool(tool_name or "", config=config)
    ):
        _reads = state.target_read_counts.get(tool_target, 0)
        _writes = state.target_write_counts.get(tool_target, 0)
        if (
            _reads >= same_target_warn_at
            and _writes == 0
            and (
                _reads == same_target_warn_at
                or (_reads - same_target_warn_at) % 3 == 0
            )
        ):
            logger.warning(
                "loop_policy check_5: same-target re-read ADVISORY. "
                "target=%s reads=%d writes=%d warn_at=%d",
                tool_target, _reads, _writes, same_target_warn_at,
            )
            return ToolLoopOutcome(
                action="warn",
                state=state,
                guidance=(
                    f"检测到同目标重复读取：{tool_target} 已被读取 {_reads} 次且从未写入。\n"
                    f"对同一目标的重读（包括重叠/平移的行区间）不会产生新信息。\n"
                    f"请立即停止重读，二选一执行（不要停下来解释或请求确认）：\n"
                    f"1. 基于已读取的内容，执行你已宣布计划中的下一步；\n"
                    f"2. 如仍缺某一特定信息，用一次 grep/pattern 定向提取，不要整文件重读。\n"
                    f"交付不完整但真实的结果，优于继续读取。"
                ),
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


def _coerce_task_spec(raw: Any) -> TaskSpec | None:
    if isinstance(raw, TaskSpec):
        return raw
    if isinstance(raw, dict):
        try:
            return TaskSpec.model_validate(raw)
        except Exception:
            return None
    return None


def update_unknown_tool_state(
    tool_calls: list[dict[str, Any]],
    registry: ToolRegistry,
    current_unknown: str | None,
    count: int,
    warned: bool,
    threshold: int,
) -> tuple[str | None, int, bool, str, str | None]:
    if threshold <= 0 or not tool_calls:
        return None, 0, False, "proceed", None

    names = []
    for call in tool_calls:
        fn = (call or {}).get("function") or {}
        name = fn.get("name", "") or ""
        names.append(name)

    unknown_names_in_turn = [name for name in names if name and name not in registry]
    if not unknown_names_in_turn:
        return None, 0, False, "proceed", None

    first_unknown = unknown_names_in_turn[0]
    if current_unknown is not None and first_unknown != current_unknown:
        count = 0
        warned = False

    current_unknown = first_unknown
    count += 1

    if warned:
        return current_unknown, count, warned, "terminate", None

    if count >= threshold:
        available = ", ".join(sorted(registry.names())) or "（无）"
        guidance = (
            f"你调用了不存在的工具 `{current_unknown}`。"
            f"可用工具：[{available}]。请停止调用 `{current_unknown}`。"
        )
        return current_unknown, count, True, "warn", guidance

    return current_unknown, count, warned, "proceed", None


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
    from prismclaw.engine.execution.dispatch.prepared_types import PreparedCall, ImmediateResult
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
