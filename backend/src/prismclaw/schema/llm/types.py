from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol

from prismclaw.schema.llm.request import PackedLLMRequest


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
    """LLM 流式输出块（纯数据）。"""

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


class LLMClient(Protocol):
    """Agent 运行时与具体 LLM 实现之间的唯一契约。

    允许 throw — 调用方负责 try/except 归约。
    这不是 Pi 的 "must not throw"，而是 Python 的务实选择。

    调用方统一使用关键字参数：
    `llm.stream(request=..., model=..., tools=..., ...)`。
    """

    default_model: str

    def stream(
        self,
        *,
        request: PackedLLMRequest,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        prompt_cache_runtime: object | None = None,
        idle_seconds: float = 0.0,
        abort_event: asyncio.Event | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[LLMStreamChunk]: ...

    async def complete(
        self,
        *,
        request: PackedLLMRequest,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        prompt_cache_runtime: object | None = None,
        idle_seconds: float = 0.0,
        abort_event: asyncio.Event | None = None,
        temperature: float | None = None,
    ) -> LLMResponse: ...


__all__ = ["LLMStreamChunk", "LLMClient", "LLMUsage", "LLMResponse"]
