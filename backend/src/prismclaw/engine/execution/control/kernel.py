"""完成控制内核：是否完成 / 是否续跑的唯一决策者。

控制权范围刻意收窄为「完成控制」：

* 工具循环策略（同调用同结果、连续失败、冻结）仍归 ``call_handling``；
* 压缩重启 / 工具全拒的降级仍归各自的既有模块，本内核只按
  ``RecoveryPolicy`` 的咨询结果下发 ``RESTART``；
* 预算（轮次 / 墙钟）是唯一先于完成判定的硬边界。

因此 ``decide`` 是单一决策点，但只对「完成」负责——宣称它统一一切会把
工具执行策略拖进完成内核。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Literal

from prismclaw.engine.execution.control.completion import (
    CompletionContract,
    ReviewResult,
)
from prismclaw.engine.execution.control.completion import (
    CompletionReviewer as CompletionReviewer,
)
from prismclaw.engine.foundation.metrics import emit
from prismclaw.schema.agent.task_runtime import EvidenceRecord

if TYPE_CHECKING:
    from prismclaw.engine.execution.checkpoint.models import RunState
    from prismclaw.schema.config.config import RunBudgetConfig

logger = logging.getLogger(__name__)

Verdict = Literal["PROCEED", "RESTART", "REPAIR", "COMPLETE", "TERMINATE"]
LoopSite = Literal["preflight", "post", "all_blocked"]


def _code_value(code: str) -> str:
    """终态码的 wire 形式。

    ``TerminalReason`` 是 ``str`` 枚举，直接 ``str()`` 会得到
    ``"TerminalReason.BUDGET_ITERATIONS"`` —— 指标标签与日志里出现这个前缀
    会让看板/告警匹配失效。统一取枚举值，普通字符串原样返回。
    """
    value = getattr(code, "value", None)
    return value if isinstance(value, str) else str(code)


class TerminalReason(StrEnum):
    """run 终态标签的闭集词汇（``rt.terminated_by`` / teardown / 轨迹通道）。

    与 ``ErrorEvent.error_code`` 严格区分：error_code 是开放集（LLMError
    code、模型申报的阻塞码等），``terminated_by`` 只允许这里的闭集标签。
    枚举成员即 ``str``，wire 格式与历史字符串完全一致，前端无需改动。
    """

    DONE = "done"
    MAX_TOKENS = "max_tokens"
    ABORTED = "aborted"
    TIMEOUT = "timeout"
    BLOCKED = "blocked"
    ERROR = "error"
    EARLY_EXIT = "early_exit"
    NO_RESPONSE_EXHAUSTED = "no_response_exhausted"
    BUDGET_ITERATIONS = "budget_iterations"
    BUDGET_SECONDS = "budget_seconds"
    BLOCKED_DECLARED = "blocked_declared"
    BLOCKED_NO_PROGRESS = "blocked_no_progress"
    BLOCKED_TOOLS_DENIED = "blocked_tools_denied"


@dataclass(frozen=True)
class LoopEvent:
    """循环事件：声明发生位置与语义，不再兼作分支选择器。

    ``is_stop`` 表示模型停止调用工具（完成申报）；``declares_blocked``
    表示本轮之前已通过 declare_blocked 工具申报阻塞。
    """

    site: LoopSite
    iteration: int = 0
    is_stop: bool = False
    declares_blocked: bool = False


@dataclass(frozen=True)
class Gap:
    code: str
    detail: str | None = None


@dataclass(frozen=True)
class Decision:
    verdict: Verdict
    code: str
    gaps: tuple[Gap, ...] = ()

    @property
    def should_continue(self) -> bool:
        return self.verdict in {"PROCEED", "RESTART", "REPAIR"}


@dataclass
class CompletionState:
    """一次 run 的完成控制状态（唯一一份）。"""

    contract: CompletionContract | None = None
    # 就绪契约（Phase 2）：区分「未构建」与「已构建且无契约（answer 类）」。
    # 契约在首个消费点惰性构建（分类任务 join + model_copy 回写模式）。
    contract_resolved: bool = False
    ledger: list[EvidenceRecord] = field(default_factory=list)
    repairs: int = 0
    advisory_nudged: bool = False


@dataclass(frozen=True)
class Budget:
    """资源上限。修复额度不在这里——它属于完成契约。

    steps / hard_s 由 ``resolve_run_budget`` 按配置预算派生，不在此配置。
    """

    max_iterations: int
    run_deadline_s: float
    run_started: float


@dataclass(frozen=True)
class RunBudget:
    """生效预算（配置上限 + 运行时派生值）。"""

    steps: int
    tool_calls: int
    soft_s: float
    hard_s: float
    reserve_s: float
    cap_s: float
    converge_tools: int


def resolve_run_budget(*, config: RunBudgetConfig) -> RunBudget:
    """按配置上限解析生效预算。

    派生值不配置：reserve_s = clamp(hard_s*0.25, 20, 120)，
    cap_s = clamp(hard_s*0.6, 90, 240)，converge_tools = max(2, tool_calls//3)。
    """
    steps = int(config.steps)
    tool_calls = int(config.tool_calls)
    soft_s = float(config.soft_s)
    hard_s = float(config.hard_s)
    reserve_s = min(max(hard_s * 0.25, 20.0), 120.0)
    cap_s = min(max(hard_s * 0.6, 90.0), 240.0)
    converge_tools = max(2, tool_calls // 3)
    return RunBudget(
        steps=steps,
        tool_calls=tool_calls,
        soft_s=soft_s,
        hard_s=hard_s,
        reserve_s=reserve_s,
        cap_s=cap_s,
        converge_tools=converge_tools,
    )


@dataclass(frozen=True)
class ControlDecision:
    """loop 控制单一评估点的输出。

    should_stop：本轮直接终止（steps / tool_calls / hard_s 任一耗尽）。
    freeze_tools：需要冻结部分工具（wrapping 冻结搜索类，last_turn 冻结非写入类）。
    is_last_turn：本轮是最后一轮（剩余 hard_s <= reserve_s 或 turns_left <= 1）。

    doom-loop 签名冻结不在此判定——由 call_handling.py Check 2 负责。
    """

    should_stop: bool
    freeze_tools: bool
    is_last_turn: bool
    turns_left: int
    tools_left: int


def evaluate_loop_control(
    *,
    run_state: RunState,
    run_budget: RunBudget,
    now: float,
    phase: str,
) -> ControlDecision:
    """loop 控制的单一评估点。

    收尾判定不落库、不做状态机：
    wrapping  = turns_left <= 2 or remaining_soft <= 0 or tools_left <= converge_tools
    last_turn = turns_left <= 1 or remaining_hard <= reserve_s or 工具预算收尾轮
    should_stop = turns_left <= 0 or remaining_hard <= 0
                  or (tools_left <= 0 且收尾轮已结束)

    工具预算耗尽时先安排一次纯文本收尾轮：模型通常正处在「拿到最后一批工具
    结果、还没输出结论」的时刻，直接终止会让整轮内容凭空消失。收尾轮的
    step 序号记录在 loop_state.tool_wrapup_step，prepare 阶段安排、stream
    阶段消费，下一轮 prepare 才真正停止。
    """
    ls = run_state.loop_state
    turns_left = run_budget.steps - ls.steps_spent
    tools_left = run_budget.tool_calls - ls.tool_calls_spent
    remaining_soft = ls.soft_at - now
    remaining_hard = ls.hard_at - now
    _wrapup_target = ls.steps_spent + 1 if phase == "prepare" else ls.steps_spent
    if tools_left <= 0 and turns_left > 0 and remaining_hard > run_budget.reserve_s:
        if phase == "prepare" and ls.tool_wrapup_step < 0:
            ls.tool_wrapup_step = _wrapup_target
        wrapup_turn = ls.tool_wrapup_step == _wrapup_target
    else:
        wrapup_turn = False
    wrapping = turns_left <= 2 or remaining_soft <= 0 or tools_left <= run_budget.converge_tools
    last_turn = turns_left <= 1 or remaining_hard <= run_budget.reserve_s or wrapup_turn
    should_stop = turns_left <= 0 or remaining_hard <= 0 or (tools_left <= 0 and not wrapup_turn)
    return ControlDecision(
        should_stop=should_stop,
        freeze_tools=wrapping or last_turn,
        is_last_turn=last_turn,
        turns_left=turns_left,
        tools_left=tools_left,
    )


class BudgetGuard:
    """轮次 / 墙钟的唯一预算权威。0 表示未设上限。"""

    _WARN_FRACTION = 0.9

    def __init__(self, budget: Budget) -> None:
        self._budget = budget

    def check(self, *, iteration: int) -> TerminalReason | None:
        budget = self._budget
        if budget.max_iterations > 0 and iteration >= budget.max_iterations:
            return TerminalReason.BUDGET_ITERATIONS
        if (
            budget.run_deadline_s > 0
            and (time.monotonic() - budget.run_started) > budget.run_deadline_s
        ):
            return TerminalReason.BUDGET_SECONDS
        return None

    def warn(self, *, iteration: int) -> TerminalReason | None:
        """临限预警（不改变裁决）。返回触发预警的资源维度。"""
        budget = self._budget
        if budget.max_iterations > 0 and iteration >= self._WARN_FRACTION * budget.max_iterations:
            return TerminalReason.BUDGET_ITERATIONS
        return None


class RecoveryPolicy:
    """恢复性重启的唯一熔断器（压缩重启 / 工具全拒）。无终态权：

    连续恢复轮超过上限时 ``consult`` 返回 ``None``，由内核转
    ``TERMINATE("error")``。链的初始值取自 ``run_state.recovery_state``
    （断点续跑不丢失），之后仅存活于本次 run。

    失败按「可自愈性」分类留额度：

    * ``self._limit`` 是**全局**恢复轮上限——压缩重启等可自愈故障共用；
    * ``NON_SELF_HEALING_LIMITS`` 给不可自愈失败单独的小额度。
      ``all_tools_denied`` 属于此类：同一批工具调用在相同策略下必然再次
      被拒，重启只会原样重发（旧实现空转 4~5 轮才熔断）。额度用尽即
      终止，不再自欺欺人地「再试一次」。

    额度与链一样在真正执行过工具的回合被 ``note_progress`` 清零——
    用户/模型换用可用工具后，新的全拒应重新获得一次重规划机会。
    """

    #: 不可自愈失败的独立额度（覆盖全局 ``limit``，取两者更严者）。
    NON_SELF_HEALING_LIMITS: dict[str, int] = {"all_tools_denied": 1}

    def __init__(
        self,
        *,
        limit: int,
        chain: Sequence[str] = (),
        per_code_limit: Mapping[str, int] | None = None,
    ) -> None:
        self._limit = limit
        self._chain: list[str] = list(chain)
        # 断点续跑：历史链里的同类失败也要计入额度，否则恢复后额度被
        # 重置成满额，旧的空转问题会以「每次重启都重新计数」形式复现。
        self._counts: dict[str, int] = {}
        for code in self._chain:
            self._counts[code] = self._counts.get(code, 0) + 1
        self._per_code = dict(self.NON_SELF_HEALING_LIMITS)
        if per_code_limit:
            self._per_code.update(per_code_limit)

    @property
    def chain(self) -> list[str]:
        return list(self._chain)

    def consult(self, code: str) -> str | None:
        """登记一次恢复请求。返回码表示放行，``None`` 表示熔断。"""
        self._chain.append(code)
        self._counts[code] = self._counts.get(code, 0) + 1
        per_code_cap = self._per_code.get(code)
        if len(self._chain) > self._limit or (
            per_code_cap is not None and self._counts[code] > per_code_cap
        ):
            logger.warning(
                "recovery_chain_exhausted: chain=%s code=%s count=%d per_code_cap=%s global_cap=%d",
                self._chain,
                code,
                self._counts[code],
                per_code_cap,
                self._limit,
            )
            return None
        return code

    def note_progress(self) -> None:
        self._chain.clear()
        self._counts.clear()


class CompletionKernel:
    """唯一完成决策者。``last_review`` 保存最近一次准则评估结果供审计留痕。"""

    def __init__(
        self,
        *,
        state: CompletionState,
        reviewer: CompletionReviewer,
        budget: BudgetGuard,
        recovery: RecoveryPolicy,
    ) -> None:
        self._state = state
        self._reviewer = reviewer
        self._budget = budget
        self._recovery = recovery
        self.last_review: ReviewResult | None = None
        # 收敛提示一次性护栏：after_tool_call 注入"请给出最终答复"提示
        # 后置位，防止后续工具回合重复注入同一个提示。内核完成判定
        # 是 contract satisfied + 模型停手——收敛提示只是引导模型
        # 说出最终答复，不是完成判定的一部分。
        self.finalize_injected: bool = False

    @property
    def state(self) -> CompletionState:
        return self._state

    @property
    def reviewer(self) -> CompletionReviewer:
        return self._reviewer

    def decide(self, event: LoopEvent) -> Decision:
        """裁决入口：委托 ``_decide`` 产出裁决，随后统一留痕（SDD §10）。

        每次裁决一条结构化日志 + ``completion.verdict_total`` 计数；
        REPAIR 额外记 ``completion.repair_total``（gap_codes 维度）。
        """
        started = time.monotonic()
        decision = self._decide(event)
        elapsed_ms = (time.monotonic() - started) * 1000.0
        contract = self._state.contract
        gap_codes = tuple(gap.code for gap in decision.gaps)
        logger.info(
            "completion_decide: site=%s iteration=%d enforcement=%s criteria=%s "
            "gap_codes=%s repairs=%d verdict=%s code=%s elapsed_ms=%.1f",
            event.site,
            event.iteration,
            contract.enforcement if contract is not None else "none",
            [criterion.kind for criterion in contract.criteria] if contract is not None else [],
            gap_codes,
            self._state.repairs,
            decision.verdict,
            _code_value(decision.code),
            elapsed_ms,
        )
        emit(
            "completion.verdict_total",
            1.0,
            kind="counter",
            labels={"verdict": decision.verdict, "code": _code_value(decision.code)},
        )
        if decision.verdict == "REPAIR":
            emit(
                "completion.repair_total",
                1.0,
                kind="counter",
                labels={"gap_codes": ",".join(gap_codes)},
            )
        return decision

    def _decide(self, event: LoopEvent) -> Decision:
        # 每次裁决只暴露本次的评估结果——预算早退不留下上一次的
        # last_review，否则治理层会为未评估的裁决写下陈旧留痕。
        self.last_review = None

        # 1. 硬边界：预算 / 恢复链熔断。独立于契约评估——预算耗尽或
        #    恢复额度用尽时没有"继续跑"的可能。
        if (
            code := self._budget.check(
                iteration=event.iteration,
            )
        ) is not None:
            return Decision("TERMINATE", code)

        if event.site == "all_blocked":
            recovery_code = self._recovery.consult("all_tools_denied")
            if recovery_code is None:
                # 全拒是不可自愈故障：额度用尽即终态，不再回到顶部重发。
                return Decision(
                    "TERMINATE",
                    TerminalReason.BLOCKED_TOOLS_DENIED,
                    (Gap("all_tools_denied", "工具调用被策略整体拒绝，重试无法改变结果。"),),
                )
            return Decision(
                "RESTART",
                recovery_code,
                (Gap("all_tools_denied_retry", "全拒后给予一次重规划机会。"),),
            )

        # 只有「工具真正执行过」的回合才算非恢复轮。若在每个事件上都
        # 清零，all_blocked 链会在下一轮 preflight 被清空，全拒熔断
        # 永远不触发（旧实现即此缺陷）。
        if not event.is_stop and event.site == "post":
            self._recovery.note_progress()

        # 2. 契约评估（每次裁决都跑——不再被 is_stop 守卫）。
        #    引擎是完成判定的唯一权威：交付物齐不齐不依赖模型自愿停手。
        contract = self._state.contract
        if contract is None:
            # 无机械完成要求：模型停手 = 完成；还想跑 = 继续。
            if event.is_stop:
                return Decision("COMPLETE", "complete_open")
            return Decision("PROCEED", "ok")

        review = self._reviewer.evaluate(contract, self._state.ledger)
        self.last_review = review

        if review.accepted:
            if event.is_stop:
                # 模型停手 + 契约齐 = 真完成。
                return Decision("COMPLETE", "complete_verified")
            # 契约齐但模型还没停手——循环继续跑一轮，让模型输出最终答复。
            # after_tool_call 会读 last_review.accepted 注入收敛提示（"不要再调
            # 工具，给出最终答复"），下一轮 preflight + is_stop 时走正常
            # COMPLETE 路径落树。内核在此不直接终止循环，避免跳过最终答复。
            return Decision("PROCEED", "contract_satisfied_waiting_final")

        # 3. 契约未 satisfied 的续跑 / REPAIR 决策。
        if not event.is_stop:
            return Decision("PROCEED", "ok")

        # 4. is_stop=True 但契约未 satisfied：治理层收尾（REPAIR / TERMINATE）。
        #    完成优先于过时的阻塞申报：带缺口的答复才算未完成。
        if event.declares_blocked:
            if self._state.repairs >= 1 or self._state.ledger:
                return Decision(
                    "TERMINATE",
                    TerminalReason.BLOCKED_DECLARED,
                    (Gap("blocked_declared", "模型申报阻塞，且已有执行尝试。"),),
                )
            # 拒绝也算一次修复，防止反复申报把 repairs 卡在 0。
            self._state.repairs += 1
            return Decision(
                "REPAIR",
                "blocked_rejected",
                (Gap("blocked_without_attempt", "尚未执行任何尝试就申报阻塞。"),),
            )

        gaps = tuple(Gap(outcome.code, outcome.describe()) for outcome in review.gaps)

        if contract.enforcement == "advisory":
            if self._state.advisory_nudged:
                return Decision("COMPLETE", "complete_advisory")
            self._state.advisory_nudged = True
            return Decision("REPAIR", "advisory_nudge", gaps)

        if self._state.repairs >= contract.max_repairs:
            return Decision("TERMINATE", TerminalReason.BLOCKED_NO_PROGRESS, gaps)

        self._state.repairs += 1
        return Decision("REPAIR", "repair", gaps)
