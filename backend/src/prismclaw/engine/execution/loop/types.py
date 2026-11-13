"""AgentLoop 类型定义。

核心类型：
    IterationFrame            — 每迭代重建的骨架迭代单元
    RunLedger                 — Run 级仅追加账本（事件流）
    RunLoopState              — 三周期状态之家（frame / ledger / Run 级对象）
    BeforeToolCallContext/Result — 工具执行前拦截
    AfterToolCallContext/Result  — 工具执行后处理
    TurnUpdate                — 轮间状态更新（压缩重启）
    PreparePort/StreamPort/ExecutePort — 三端口（构造期必填，P4）
    HITLCheckResult           — HITL 检测结果 (B/S 架构独有)
    LoopConfig                — 三端口 + 完成内核绑定 + 可选插槽
    EventSink                 — 事件输出接口
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from prismclaw.engine.compression.cache_block_packing import PromptCacheRuntimeContract
    from prismclaw.engine.execution.checkpoint import RunSnapshot, RunStateMachine
    from prismclaw.engine.execution.checkpoint.models import RunState
    from prismclaw.engine.execution.control.kernel import Decision
    from prismclaw.engine.execution.dispatch.pipeline import (
        LLMStreamError,
        LLMStreamNoResponse,
        LLMStreamResult,
        LLMStreamRetry,
    )
    from prismclaw.engine.execution.loop.arbiter import RunContext
    from prismclaw.engine.execution.loop.ownership import _RunOwnershipLease
    from prismclaw.engine.execution.query.pre_request_pipeline import PreparedTurn
    from prismclaw.engine.foundation.telemetry.profiler import TurnProfiler
    from prismclaw.schema.llm.types import LLMResponse
    from prismclaw.engine.state.tool_loop_state import ToolLoopState
    from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext
    from prismclaw.schema.agent.session import ProjectionInputMessage
    from prismclaw.schema.agent.task_runtime import EvidenceRecord, TaskRuntimeState
    from prismclaw.schema.content.content_types import ToolResult
    from prismclaw.schema.content.streaming import Done, ErrorEvent, ToolClarifyRequest
    from prismclaw.schema.services.skills import SkillInvocationState
    from prismclaw.schema.session.types import SessionTree


# ---------------------------------------------------------------------------
# LoopTiming — 性能基线采集
# ---------------------------------------------------------------------------

@dataclass
class LoopTiming:
    """单次迭代的计时数据。

    在 run_loop() 的关键路径打点，只在走到循环底部 bookkeeping 时记录；
    无工具调用的回合会跳过 bookkeeping（见 TurnPerf 说明）。
    所有时间单位为毫秒。
    """
    iteration: int = 0
    pre_request_pipeline_ms: float = 0.0
    llm_stream_ms: float = 0.0
    tool_execution_ms: float = 0.0
    total_iteration_ms: float = 0.0


# ---------------------------------------------------------------------------
# TurnPerf — 回合级性能聚合
# ---------------------------------------------------------------------------

@dataclass
class TurnPerf:
    """一次 run_loop 的累加计时（跨 iteration 聚合，不随迭代重置）。

    与 ``LoopTiming`` 的分工：LoopTiming 保存"当前 iteration"的读数，只在
    走到循环底部 bookkeeping 时记录——纯文本回合（无工具调用）会跳过
    bookkeeping，因此对咨询类回合无感。TurnPerf 在**计时的当处**累加
    （prepare / stream / execute），与后续控制流分支无关。

    用途：回合结束后单行汇总。只做度量，不参与任何决策。
    """

    # 单调时钟起点（run_loop 首次进入时打点）
    started_at: float = 0.0
    # 实际发生的 LLM 调用数（含重试/失败重发），同时作为步数真值
    llm_calls: int = 0
    # 实际执行的工具调用数（不含被 before_tool_call 拦截的）
    tool_calls: int = 0
    # 三段主耗时累加（毫秒）
    prep_ms: float = 0.0
    llm_ms: float = 0.0
    tools_ms: float = 0.0

    def wall_ms(self) -> float:
        """回合总墙钟耗时（毫秒）。未打点返回 0。"""
        if self.started_at <= 0.0:
            return 0.0
        import time as _t

        return max(0.0, (_t.monotonic() - self.started_at) * 1000.0)

    def loop_self_ms(self) -> float:
        """loop 自身开销 = 墙钟 - 三段主耗时（含内核/治理/hook/落树）。"""
        return max(0.0, self.wall_ms() - self.prep_ms - self.llm_ms - self.tools_ms)


# ---------------------------------------------------------------------------
# IterationFrame — 每迭代重建的骨架迭代单元
# ---------------------------------------------------------------------------

@dataclass
class IterationFrame:
    """单次迭代的可变单元（骨架重置点归零；闭包不得跨迭代缓存本单元）。

    归零点全部在 run_loop 函数体内（冻结契约）：
    - Step 3 前：last_tool_results / blocked_tool_results / frozen_counted_this_turn
    - bookkeeping 尾：llm_outcome / llm_response
    - prepare / after_tool_call 出口：pending_messages 全量替换

    pending_messages 是 LLM 注入通道（预算警告、工具循环指导等），
    不是 WebSocket 事件，不 yield；由 prepare 端口每迭代合并进
    query_loop_state 后清空。
    """
    pending_messages: list[ProjectionInputMessage] = field(default_factory=list)
    llm_outcome: (
        LLMStreamResult | LLMStreamError | LLMStreamRetry | LLMStreamNoResponse | None
    ) = None
    llm_response: LLMResponse | None = None
    llm_start_monotonic: float = 0.0
    last_tool_results: list[ToolResult] = field(default_factory=list)
    blocked_tool_results: list[Any] = field(default_factory=list)
    frozen_counted_this_turn: set[str] = field(default_factory=set)
    # 本 step 是否触发过 max_tokens（终止判定用）
    max_tokens_hit_in_step: bool = False
    # 本批次所有 parsed call ids（abort 闭合用）
    _pending_tool_call_ids: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# RunLedger — Run 级仅追加账本（事件流）
# ---------------------------------------------------------------------------

@dataclass
class RunLedger:
    """Run 级仅追加账本。

    所有字段单调累加/追加，不回退、不按迭代重置：
    - evidence_ledger：工具证据记录（execute_tools 内唯一追加点）
    - total_*_tokens：跨迭代 token 累计（stream_llm 内累加）
    """

    evidence_ledger: list[EvidenceRecord] = field(default_factory=list)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_creation_tokens: int = 0
    total_cache_read_tokens: int = 0


# ---------------------------------------------------------------------------
# RunLoopState — 三周期状态之家
# ---------------------------------------------------------------------------

@dataclass
class RunLoopState:
    """三周期状态之家（SDD §3.6 三分）。

    字段按生命周期归位：
    - ``frame``（IterationFrame）：每迭代重建的骨架迭代单元
    - ``ledger``（RunLedger）：Run 级仅追加账本
    - 其余扁平字段：Run 级状态机对象 / 语义演化对象 / 终止事件

    run_loop 以扁平名访问迭代单元（冻结契约，SDD §2.1/§5.1）；
    文件底部的 property 是唯一的扁平访问层，其余代码一律直写
    ``state.frame.*`` / ``state.ledger.*``。
    """

    # ── Run 级状态机对象（构造时注入，跨迭代存活）──
    iteration: int = 0
    ctx: RunContext | None = None
    tree: SessionTree | None = None
    snapshot: RunSnapshot | None = None
    run_state: RunState | None = None
    state_machine: RunStateMachine | None = None
    tool_loop_state: ToolLoopState | None = None
    task_runtime: TaskRuntimeState | None = None

    # ── 语义演化对象（只收紧不回退）──
    active_skill_invocation: SkillInvocationState | None = None
    # 技能上下文修饰的注入队列：execute_tools 内由技能结果全量替换，
    # prepare 端口每迭代消费进 prompt meta 区。替换=技能状态演化，
    # 不回退旧值（非失效标志，是功能通道）。
    pending_meta_messages: list[ProjectionInputMessage] | None = None

    # ── 运行时附属 ──
    tool_ctx: ToolContext | None = None
    ownership_lease: _RunOwnershipLease | None = None
    profiler: TurnProfiler | None = None
    prompt_cache_runtime: PromptCacheRuntimeContract | None = None

    # ── Pipeline 产物（prepare 端口写入，stream 端口与内核绑定读取）──
    prepared_turn: PreparedTurn | None = None

    # ── 终止事件（should_continue 写入，骨架 yield）──
    terminal_error: ErrorEvent | None = None
    done_event: Done | None = None

    # ── 三周期载体 ──
    frame: IterationFrame = field(default_factory=IterationFrame)
    ledger: RunLedger = field(default_factory=RunLedger)

    # ── 性能基线采集（骨架打点；两个计数对象本身不随迭代重置）──
    timing: LoopTiming = field(default_factory=LoopTiming)
    # 回合级累加（跨迭代，决策不读，仅度量）
    perf: TurnPerf = field(default_factory=TurnPerf)

    # ───────────────────────────────────────────────────────────────
    # 骨架零 diff 委托层（run_loop 函数体的扁平访问契约，勿改签名）
    # 闭包与适配器直写 state.frame.*，此层只服务冻结的骨架函数体。
    # ───────────────────────────────────────────────────────────────

    @property
    def pending_messages(self) -> list[ProjectionInputMessage]:
        return self.frame.pending_messages

    @pending_messages.setter
    def pending_messages(self, value: list[ProjectionInputMessage]) -> None:
        self.frame.pending_messages = value

    @property
    def llm_outcome(self):
        return self.frame.llm_outcome

    @llm_outcome.setter
    def llm_outcome(self, value) -> None:
        self.frame.llm_outcome = value

    @property
    def llm_response(self) -> LLMResponse | None:
        return self.frame.llm_response

    @llm_response.setter
    def llm_response(self, value: LLMResponse | None) -> None:
        self.frame.llm_response = value

    @property
    def last_tool_results(self) -> list[ToolResult]:
        return self.frame.last_tool_results

    @last_tool_results.setter
    def last_tool_results(self, value: list[ToolResult]) -> None:
        self.frame.last_tool_results = value

    @property
    def blocked_tool_results(self) -> list[Any]:
        return self.frame.blocked_tool_results

    @blocked_tool_results.setter
    def blocked_tool_results(self, value: list[Any]) -> None:
        self.frame.blocked_tool_results = value

    @property
    def frozen_counted_this_turn(self) -> set[str]:
        return self.frame.frozen_counted_this_turn

    @frozen_counted_this_turn.setter
    def frozen_counted_this_turn(self, value: set[str]) -> None:
        self.frame.frozen_counted_this_turn = value

    @property
    def max_tokens_hit_in_step(self) -> bool:
        return self.frame.max_tokens_hit_in_step

    @max_tokens_hit_in_step.setter
    def max_tokens_hit_in_step(self, value: bool) -> None:
        self.frame.max_tokens_hit_in_step = value

    @property
    def _pending_tool_call_ids(self) -> list[str]:
        return self.frame._pending_tool_call_ids

    @_pending_tool_call_ids.setter
    def _pending_tool_call_ids(self, value: list[str]) -> None:
        self.frame._pending_tool_call_ids = value


# ---------------------------------------------------------------------------
# BeforeToolCallContext/Result — 工具执行前拦截
# ---------------------------------------------------------------------------

@dataclass
class BeforeToolCallContext:
    """工具执行前检查的上下文。"""
    tool_call: dict[str, Any] | None = None
    args: dict[str, Any] | None = None
    state: RunLoopState | None = None


@dataclass
class BeforeToolCallResult:
    """工具执行前检查的结果。

    block=True  → 该工具不执行，返回即时错误结果
    block=False → 该工具正常执行
    """
    block: bool = False
    reason: str | None = None


# ---------------------------------------------------------------------------
# AfterToolCallContext/Result — 工具执行后处理
# ---------------------------------------------------------------------------

@dataclass
class AfterToolCallContext:
    """工具执行后处理的上下文。

    批量处理一轮的所有工具结果（POST_TOOL hooks 等）。结果与 assistant
    消息经 ``state``（``last_tool_results`` / ``llm_response``）读取。
    """
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    state: RunLoopState | None = None


@dataclass
class AfterToolCallResult:
    """工具执行后处理的结果。

    唯一消费通道：inject_messages（轮间消息注入，骨架写入
    state.pending_messages）；不注入则返回 None。
    """
    inject_messages: list[ProjectionInputMessage] | None = None


# ---------------------------------------------------------------------------
# TurnUpdate — 轮间状态更新
# ---------------------------------------------------------------------------

@dataclass
class TurnUpdate:
    """轮间状态更新（压缩重启的唯一载体）。

    restart=True 表示压缩重启（不执行工具，直接进入下一轮）：
    CompactionEntry 已落树，下轮 prepare 从 tree 重建即含摘要。
    pending_messages 若提供则作为重放注入（restart 前已合并进本轮
    query_loop_state 的一次性注入尚未送达模型，不重放即永久丢失）。

    不携带任何上下文缓存/失效字段：上下文重建是每迭代从 tree 的
    纯投影（树是唯一真相源），无第二份可更新的副本。
    """
    restart: bool = False
    pending_messages: list[ProjectionInputMessage] | None = None


# ---------------------------------------------------------------------------
# HITLCheckResult — B/S 架构独有
# ---------------------------------------------------------------------------

@dataclass
class HITLCheckResult:
    """HITL 检测结果。AskUserQuestion 需要跨 yield 边界处理。"""
    needs_clarification: bool = False
    # ToolClarifyRequest 事件对象：被骨架 yield 给前端，且
    # wait_for_clarification 用其 approval_id 等待用户回答。
    clarification_request: ToolClarifyRequest | None = None


# ---------------------------------------------------------------------------
# 三端口 — 阶段适配器（构造期必填，缺适配器即 TypeError）
# ---------------------------------------------------------------------------

@dataclass
class PreparePort:
    """上下文准备端口（Iteration 级投影；Run 级缓存由投影实现内聚）。

    每迭代调用 ``prepare_next_turn`` 产出下一轮 LLM 上下文
    （pipeline 全链），或返回 TurnUpdate（压缩重启 + 注入重放）。
    """
    prepare_next_turn: Callable[[RunLoopState], Awaitable[TurnUpdate | None]]


@dataclass
class StreamPort:
    """LLM 流式端口。

    abort / 墙钟终局检查在入口完成（硬约束），随后调用 LLM 并回填
    frame.llm_outcome / frame.llm_response，用量累计进 ledger。
    """
    stream_llm: Callable[[RunLoopState], AsyncIterator]


@dataclass
class ExecutePort:
    """工具执行端口（拦截→执行→后处理→树持久化闭环，自包含）。

    工具结果回填（frame.last_tool_results）在本端口内完成（硬约束）。
    HITL 插槽（check_hitl / wait_for_clarification / persist_clarified_turn）
    与全拒持久化（persist_denied_turn）为 B/S 架构独有，可选。
    """
    execute_tools: Callable[..., AsyncIterator]
    before_tool_call: (
        Callable[[BeforeToolCallContext], Awaitable[BeforeToolCallResult | None]] | None
    ) = None
    after_tool_call: (
        Callable[[AfterToolCallContext], Awaitable[AfterToolCallResult | None]] | None
    ) = None
    persist_denied_turn: Callable[[RunLoopState, Any, list[Any]], Awaitable[None]] | None = None
    persist_clarified_turn: Callable[..., Awaitable[None]] | None = None
    check_hitl: Callable[[list[Any]], HITLCheckResult | None] | None = None
    # 等待用户回答：返回用户作答文本；**返回 None 表示非用户作答**
    # （abort / stale / 会话结束）——骨架据此终止本轮，绝不用假答案继续。
    # 等待本身无超时：只有用户提交才恢复执行。**实现不落树 answer**——
    # 落树由 persist_clarified_turn 统一负责（顺序：assistant → tool 结果
    # → user answer；先落会把 user 消息插在提问之前并隔断 tool 配对）。
    wait_for_clarification: Callable[[HITLCheckResult], Awaitable[str | None]] | None = None


# ---------------------------------------------------------------------------
# LoopConfig — 三端口 + 完成内核绑定 + 可选插槽
# ---------------------------------------------------------------------------

@dataclass
class LoopConfig:
    """循环行为配置。

    三个端口构造期必填（P4 fast fail：缺端口即 TypeError，取代运行中
    静默少跑一个阶段）：

    - ``prepare``：上下文准备端口（主循环 = PreRequestPipeline 全链；
      subagent = 无重装配的空实现）
    - ``stream``：LLM 流式端口（abort/墙钟检查在入口）
    - ``execute``：工具执行端口（拦截/执行/后处理/树持久化闭环）

    ``should_continue`` 是完成内核（CompletionKernel）的唯一绑定——
    完成裁决单点，绑定闭包只做翻译，不含独立决策逻辑。
    ``get_steering_messages`` 是 B/S 独有插槽（headless/subagent 无）。

    run_loop 以扁平名消费端口闭包（SDD §5.1 冻结契约）——
    下方 property 是唯一的扁平访问层，其余代码一律直取端口对象。
    """
    prepare: PreparePort
    stream: StreamPort
    execute: ExecutePort
    should_continue: Callable[..., Awaitable[Decision]]
    # steering 消费（B/S 独有）：iteration 边界取一批用户中途注入文本。
    # 闭包负责 drain 会话队列 + 落树 role="user" MessageEntry（原样内容、
    # 无前缀）；steering 随树进入下一轮 prepare 投影，无需失效标志。
    # None 或返回空列表 = 行为完全不变。
    get_steering_messages: Callable[[], Awaitable[list[str]]] | None = None

    # ───────────────────────────────────────────────────────────────
    # 骨架零 diff 委托层（run_loop 函数体的扁平访问契约，勿改签名）
    # ───────────────────────────────────────────────────────────────

    @property
    def prepare_next_turn(self):
        return self.prepare.prepare_next_turn

    @property
    def stream_llm(self):
        return self.stream.stream_llm

    @property
    def before_tool_call(self):
        return self.execute.before_tool_call

    @property
    def after_tool_call(self):
        return self.execute.after_tool_call

    @property
    def execute_tools(self):
        return self.execute.execute_tools

    @property
    def persist_denied_turn(self):
        return self.execute.persist_denied_turn

    @property
    def persist_clarified_turn(self):
        return self.execute.persist_clarified_turn

    @property
    def check_hitl(self):
        return self.execute.check_hitl

    @property
    def wait_for_clarification(self):
        return self.execute.wait_for_clarification


# ---------------------------------------------------------------------------
# EventSink — 事件输出接口
# ---------------------------------------------------------------------------

EventSink = Callable[[object], None]