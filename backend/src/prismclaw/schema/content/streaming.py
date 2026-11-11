from __future__ import annotations

from typing import Any, Literal, Union

from pydantic import BaseModel, Field

from prismclaw.schema.content.content_types import ToolResult


class TextChunk(BaseModel):
    type: Literal["text_chunk"] = "text_chunk"
    text: str


class ReasoningChunk(BaseModel):
    """A streaming chunk of model reasoning (e.g. ``reasoning_content`` from
    GLM/Qwen/DeepSeek, ``thinking`` from Anthropic). Streamed alongside
    :class:`TextChunk` so the UI can render a collapsible "thinking" region
    live, filling the perceived latency before the visible answer starts.

    Reasoning is **live-only** — it is not persisted into ``MessageEntry``
    (which has no reasoning field) and thus not restored on reload. The
    thinking process is transient by nature.
    """

    type: Literal["reasoning_chunk"] = "reasoning_chunk"
    reasoning: str


class ToolCallStart(BaseModel):
    type: Literal["tool_call_start"] = "tool_call_start"
    tool_call_id: str
    name: str
    arguments: dict[str, Any]
    # P1.1：归属到当前执行步骤；无计划/无 step 时为 None（孤儿操作归 orphan_operations）。
    step_id: str | None = None


class ToolCallEnd(BaseModel):
    type: Literal["tool_call_end"] = "tool_call_end"
    tool_call_id: str
    result: ToolResult
    # P1.1：与 ToolCallStart 同义归属（消费者 Projector 在 P1.2 启用）。
    step_id: str | None = None


class HeartbeatDetail(BaseModel):
    """心跳携带的进度细节；两者皆 None 时表示纯 time-based 兜底（无增量信息）。"""

    partial_tool_name: str | None = None  # delta 可见时："Write"
    chars_so_far: int | None = None       # delta 可见时：累计参数字符数


class HeartbeatEvent(BaseModel):
    """P0.2 消假死心跳：在长参数生成 / 工具执行 / compaction 期间周期性发出，
    让前端有持续、有语义的进度反馈。

    不落库、不进 timeline 持久化（纯执行期瞬态）。phase 与 `_log_exec_phase`
    的相位一致（generating/executing/compacting/waiting_llm）。
    """

    type: Literal["heartbeat"] = "heartbeat"
    phase: Literal["generating", "executing", "compacting", "waiting_llm", "awaiting_clarification"]
    detail: HeartbeatDetail = Field(default_factory=HeartbeatDetail)
    elapsed_s: float


class IterationBoundary(BaseModel):
    """Runner 迭代轮次边界信号。Projector 用它切段 reasoning：
    - ``kind="llm_start"``：后续 ``ReasoningChunk`` / ``ToolCallStart``
      均归属于 ``iteration``。该段 reasoning 最终内嵌到本轮触发的
      ``StepNode`` / ``OrphanOperationsNode`` 卡片内部，而非独立节点。
    - ``kind="llm_end"``：当前段 LLM 输出结束（可选，用于前端结束 spinner）。
    """

    type: Literal["iteration_boundary"] = "iteration_boundary"
    iteration: int
    kind: Literal["llm_start", "llm_end"] = "llm_start"


class Done(BaseModel):
    type: Literal["done"] = "done"
    final_message: str
    assistant_message_id: str | None = None
    usage: dict[str, int] = Field(default_factory=dict)


class ErrorEvent(BaseModel):
    type: Literal["error"] = "error"
    error_code: str
    message: str


class ToolClarifyRequest(BaseModel):
    """执行中途向用户提问并等待回答（HITL）。

    由 runner 在识别到 AskUserQuestion 工具调用时 yield；前端据此渲染交互式
    问答卡片，用户回答后通过 ``tool.clarify`` WS 消息回传，runner 的等待解除、
    从同一轮执行续跑。"""

    type: Literal["tool_clarify_request"] = "tool_clarify_request"
    approval_id: str
    run_id: str
    turn_id: str
    tool_call_id: str
    tool_name: str = "ask_user_question"
    questions: list[dict[str, Any]] = Field(default_factory=list)


SubagentPartialStatus = Literal[
    "thinking",
    "tool_calling",
    "tool_running",
    "completed",
    "failed",
    "aborted",
]


class SubagentProgressItem(BaseModel):
    model_config = {"extra": "forbid"}

    sub_session_id: str
    subagent_name: str
    turn: int = Field(ge=0)
    max_turns: int = Field(ge=1)
    partial_status: SubagentPartialStatus
    current_tool: str | None = None
    active_skill_name: str | None = None
    invoked_skill_names: list[str] = Field(default_factory=list)
    duration_so_far: float = Field(ge=0.0)
    handoff_chain_id: str | None = None
    from_subagent: str | None = None


class SubagentProgressBatch(BaseModel):
    type: Literal["subagent_progress"] = "subagent_progress"
    progress_batch_id: str
    batch_seq: int = Field(ge=1)
    items: list[SubagentProgressItem] = Field(default_factory=list)


AgentEvent = Union[
    TextChunk,
    ReasoningChunk,
    ToolCallStart,
    ToolCallEnd,
    IterationBoundary,
    HeartbeatEvent,
    Done,
    ErrorEvent,
    ToolClarifyRequest,
    SubagentProgressBatch,
]
