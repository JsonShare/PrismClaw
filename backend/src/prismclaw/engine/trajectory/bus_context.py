"""Trajectory 运行时上下文 —— runner（写）与读取方共享的单一中立模块。

取代原 runner.py 内私有定义的 ``_trajectory_runtime`` ContextVar +
``_traj_runtime_ctx()`` 辅助函数。归属 core（而非 trajectory 包）的原因：
Phase 0' 解耦铁律规定依赖箭头只能 trajectory → core，反向禁止——本模块
是 bus 事件的发布身份载体，与 bus_events.py 同级，core-owned。

搬到独立模块的动机：

1. **消除循环 import**：原实现中 context_helpers.py / turn_preparation.py
   只能通过函数内 ``from prismclaw.engine.execution.loop.runner import _traj_runtime_ctx``
   惰性导入（runner → bootstrap → context_helpers 的链路上直接顶层 import
   会成环）。现在所有读取方直接 import 本模块。
2. **bootstrap 需要读**：assistant MessageEntry 落库时（bootstrap._append），
   LLM 响应已返回但指标只存在于 runner 局部变量中 —— 通过 ctx 的
   ``llm_metrics`` 快照传递，使 ConversationNodeAdded 一个事件携带
   完整的 cell 数据（详情全文 + 指标 + 时间区间），cell 写入收敛为
   单次 INSERT。
3. **工具时长**：``tool_call_starts`` 由工具调度点写入（call_id → 开始
   时刻），tool entry 落库时读取并 pop。

生命周期：runner 每迭代开始 set 新对象；turn teardown reset。
ContextVar 持有同一可变对象，迭代内由 runner 陆续填充。
"""
from __future__ import annotations

import time
from contextvars import ContextVar
from dataclasses import dataclass, field


@dataclass(slots=True)
class LLMMetricsSnapshot:
    """一次 LLM 调用的响应侧指标快照。

    runner 在响应返回后构建一次，被两处消费：
    - ``LLMResponseReceived`` 事件（迭代级 RequestAuditRecord 链路）
    - ``ConversationNodeAdded`` 事件（assistant cell 指标）

    ttft / total / decoding 算术在此集中，调用方不再各自重复计算。
    """

    model: str | None
    finish_reason: str | None
    usage_input_tokens: int | None
    usage_output_tokens: int | None
    usage_cache_read_tokens: int | None
    usage_cache_write_tokens: int | None
    usage_reasoning_tokens: int | None
    ttft_ms: float | None
    total_ms: float | None
    # epoch 毫秒；started 由 completed - total 反推（llm_start 是 monotonic）
    started_at_ms: int | None
    completed_at_ms: int | None

    @property
    def decoding_ms(self) -> float | None:
        if self.ttft_ms is None or self.total_ms is None:
            return None
        delta = self.total_ms - self.ttft_ms
        return delta if delta > 0 else None

    @classmethod
    def build(
        cls,
        *,
        model: str | None,
        finish_reason: str | None,
        usage_input_tokens: int | None,
        usage_output_tokens: int | None,
        usage_cache_read_tokens: int | None,
        usage_cache_write_tokens: int | None,
        usage_reasoning_tokens: int | None,
        llm_start: float,
        first_token_at: float | None,
        llm_end: float,
    ) -> LLMMetricsSnapshot:
        """由 monotonic 计时三元组构建快照。

        ``llm_start``/``first_token_at``/``llm_end`` 均为 time.monotonic()
        读数；epoch 时刻由当前 wall-clock 反推。
        """
        now_ms = int(time.time() * 1000)
        total_ms = (llm_end - llm_start) * 1000.0
        ttft_ms = (
            (first_token_at - llm_start) * 1000.0
            if first_token_at is not None
            else None
        )
        return cls(
            model=model,
            finish_reason=finish_reason,
            usage_input_tokens=usage_input_tokens,
            usage_output_tokens=usage_output_tokens,
            usage_cache_read_tokens=usage_cache_read_tokens,
            usage_cache_write_tokens=usage_cache_write_tokens,
            usage_reasoning_tokens=usage_reasoning_tokens,
            ttft_ms=ttft_ms,
            total_ms=total_ms,
            started_at_ms=now_ms - int(total_ms),
            completed_at_ms=now_ms,
        )


@dataclass
class TrajectoryRuntimeCtx:
    """一个 LLM 迭代内的轨迹上下文。"""

    session_id: str
    turn: int
    iteration: int
    # runner 在响应返回后填充；assistant _append 读取
    llm_metrics: LLMMetricsSnapshot | None = None
    # 本迭代 LLM 响应的完整 reasoning 文本；assistant _append 读取为
    # thinking_detail（reasoning 以 ReasoningChunk 流式发出、不 stash 在
    # entry.content 上，entry 无需改持久化格式）
    assistant_reasoning: str | None = None
    # 工具调度点写入 call_id → 开始时刻（epoch ms）；tool _append 读取并 pop
    tool_call_starts: dict[str, int] = field(default_factory=dict)


trajectory_runtime: ContextVar[TrajectoryRuntimeCtx | None] = ContextVar(
    "prismclaw.trajectory.runtime",
    default=None,
)


def current_runtime() -> TrajectoryRuntimeCtx | None:
    """返回当前迭代的 ctx；runner 循环之外返回 None。

    调用方约定：无身份（None）即不发布轨迹事件 —— 不存在
    "合成假 session_id 事件"的旧回退（bus 为 per-session，假 id 事件
    只会被订阅方丢弃）。
    """
    return trajectory_runtime.get(None)
