"""完成控制内核：是否完成 / 是否续跑的唯一决策者。

控制权范围刻意收窄为「完成控制」：

* 工具循环策略（同调用同结果、连续失败、冻结）仍归 ``call_handling``；
* 压缩重启 / 工具全拒的降级仍归各自的既有模块，本内核只按
  ``RecoveryPolicy`` 的咨询结果下发 ``RESTART``；
* 预算（轮次 / 墙钟 / 金额）是唯一先于完成判定的硬边界。

因此 ``decide`` 是单一决策点，但只对「完成」负责——宣称它统一一切会把
工具执行策略拖进完成内核。
"""
from __future__ import annotations

import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal

from prismclaw.engine.execution.control.completion import (
    CompletionContract,
    CompletionReviewer,
    ReviewResult,
)
from prismclaw.engine.foundation.metrics import emit
from prismclaw.schema.agent.task_runtime import EvidenceRecord

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


class TerminalReason(str, Enum):
    """run 终态标签的闭集词汇（``ctx.terminated_by`` / teardown / 轨迹通道）。

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
    BUDGET_USD = "budget_usd"
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
    budget_warned: bool = False


@dataclass(frozen=True)
class Budget:
    """资源上限。修复额度不在这里——它属于完成契约。

    单一轮次上限（``agent.max_iterations``）而非按模式分档：答案/分析类
    的快速收敛由「无契约 ⇒ 首次停止即 COMPLETE」结构保证，不需要另一套
    按模式计数的阈值；分档只会给合法多工具任务制造新的误杀面。
    """

    max_iterations: int
    run_deadline_s: float
    run_started: float
    max_usd: float | None


class BudgetGuard:
    """轮次 / 墙钟 / 金额的唯一预算权威。0 或 None 表示未设上限。"""

    _WARN_FRACTION = 0.9

    def __init__(self, budget: Budget) -> None:
        self._budget = budget

    def check(self, *, iteration: int, usd_cost: float) -> TerminalReason | None:
        budget = self._budget
        if budget.max_iterations > 0 and iteration >= budget.max_iterations:
            return TerminalReason.BUDGET_ITERATIONS
        if budget.run_deadline_s > 0 and (
            time.monotonic() - budget.run_started
        ) > budget.run_deadline_s:
            return TerminalReason.BUDGET_SECONDS
        if budget.max_usd is not None and usd_cost > budget.max_usd:
            return TerminalReason.BUDGET_USD
        return None

    def warn(self, *, iteration: int, usd_cost: float) -> TerminalReason | None:
        """临限预警（不改变裁决）。返回触发预警的资源维度。"""
        budget = self._budget
        if budget.max_iterations > 0 and iteration >= self._WARN_FRACTION * budget.max_iterations:
            return TerminalReason.BUDGET_ITERATIONS
        if budget.max_usd is not None and usd_cost >= self._WARN_FRACTION * budget.max_usd:
            return TerminalReason.BUDGET_USD
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
                "recovery_chain_exhausted: chain=%s code=%s count=%d "
                "per_code_cap=%s global_cap=%d",
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

    @property
    def state(self) -> CompletionState:
        return self._state

    def decide(self, event: LoopEvent, *, usd_cost: float = 0.0) -> Decision:
        """裁决入口：委托 ``_decide`` 产出裁决，随后统一留痕（SDD §10）。

        每次裁决一条结构化日志 + ``completion.verdict_total`` 计数；
        REPAIR 额外记 ``completion.repair_total``（gap_codes 维度）。
        """
        started = time.monotonic()
        decision = self._decide(event, usd_cost=usd_cost)
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

    def _decide(self, event: LoopEvent, *, usd_cost: float = 0.0) -> Decision:
        # 每次裁决只暴露本次的评估结果：早退分支（预算 / 非停止）不留下
        # 上一次的 last_review，否则治理层会为未评估的裁决写下陈旧留痕。
        self.last_review = None

        if (code := self._budget.check(
            iteration=event.iteration, usd_cost=usd_cost,
        )) is not None:
            return Decision("TERMINATE", code)

        if not event.is_stop:
            if event.site == "all_blocked":
                code = self._recovery.consult("all_tools_denied")
                if code is None:
                    # 全拒是不可自愈故障：额度用尽即终态，不再回到顶部重发。
                    return Decision(
                        "TERMINATE",
                        TerminalReason.BLOCKED_TOOLS_DENIED,
                        (Gap("all_tools_denied", "工具调用被策略整体拒绝，重试无法改变结果。"),),
                    )
                return Decision(
                    "RESTART",
                    code,
                    (Gap("all_tools_denied_retry", "全拒后给予一次重规划机会。"),),
                )
            if event.site == "post":
                # 只有「工具真正执行过」的回合才算非恢复轮。若在每个
                # 非停止事件上都清零，all_blocked 链会在下一轮 preflight
                # 被清空，全拒熔断永远不触发（旧实现即此缺陷）。
                self._recovery.note_progress()
            return Decision("PROCEED", "ok")

        if self._state.contract is None:
            # 无完成要求：交付物是文本，机械侧无产物可核。
            return Decision("COMPLETE", "complete_open")

        review = self._reviewer.evaluate(self._state.contract, self._state.ledger)
        self.last_review = review

        # 完成优先于过时的阻塞申报：带缺口的答复才算未完成。
        if event.declares_blocked and not review.accepted:
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

        if review.accepted:
            return Decision("COMPLETE", "complete_verified")

        gaps = tuple(
            Gap(outcome.code, outcome.describe()) for outcome in review.gaps
        )

        if self._state.contract.enforcement == "advisory":
            if self._state.advisory_nudged:
                return Decision("COMPLETE", "complete_advisory")
            self._state.advisory_nudged = True
            return Decision("REPAIR", "advisory_nudge", gaps)

        if self._state.repairs >= self._state.contract.max_repairs:
            return Decision("TERMINATE", TerminalReason.BLOCKED_NO_PROGRESS, gaps)

        self._state.repairs += 1
        return Decision("REPAIR", "repair", gaps)
