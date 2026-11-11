from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Protocol


@dataclass
class LLMUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    reasoning_tokens: int = 0


@dataclass
class LLMStreamChunk:
    """LLM 流式输出块。对齐 core/agent/llm.py LLMStreamChunk 的纯数据类。"""
    text_delta: str = ""
    reasoning_delta: str = ""
    tool_call_deltas: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str | None = None
    usage: LLMUsage | None = None


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[dict[str, Any]]
    usage: LLMUsage
    finish_reason: str | None
    reasoning: str = ""


class LLMStreamFn(Protocol):
    """Agent 运行时与具体 LLM 实现之间的唯一契约。

    允许 throw — 调用方负责 try/except 归约。
    这不是 Pi 的 "must not throw"，而是 Python 的务实选择。

    签名对齐：runner_pipeline.py L196 的调用方式为
    deps.llm.stream(request=..., model=..., tools=..., ...)。
    LLMStreamFn 定义 stream() 方法匹配此调用模式，
    runner_pipeline.py 零改动。
    """

    default_model: str

    async def stream(
        self,
        request: Any,
        model: str,
        tools: list[dict] | None = None,
        prompt_cache_runtime: Any | None = None,
        idle_seconds: float = 0.0,
        abort_event: Any | None = None,
        temperature: float | None = None,
        *,
        provider_hints: dict[str, Any] | None = None,
    ) -> AsyncIterator[LLMStreamChunk]: ...

    async def __call__(
        self,
        request: Any,
        model: str,
        tools: list[dict] | None = None,
        prompt_cache_runtime: Any | None = None,
        idle_seconds: float = 0.0,
        abort_event: Any | None = None,
        temperature: float | None = None,
        *,
        provider_hints: dict[str, Any] | None = None,
    ) -> AsyncIterator[LLMStreamChunk]: ...

    async def complete(
        self,
        *,
        request: Any,
        model: str | None = None,
        tools: list[dict] | None = None,
        prompt_cache_runtime: Any | None = None,
        idle_seconds: float = 0.0,
        abort_event: Any | None = None,
        temperature: float | None = None,
    ) -> Any: ...


__all__ = ["LLMStreamChunk", "LLMStreamFn", "LLMUsage", "LLMResponse"]
