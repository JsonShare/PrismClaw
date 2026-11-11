"""Streaming tool executor for concurrent tool execution.

Implements the streaming tool execution pattern from the reference implementation:
- Safe tools (read-only: grep, glob, read, configured MCP search tools, etc.) run in parallel,
  bounded by an ``asyncio.Semaphore`` (default concurrency 10).
- Non-safe tools (write: write, bash, edit, etc.) run exclusively under a
  single FIFO ``asyncio.Lock``.
- Results are yielded in the order tools were added (add_tool order).
- discard() cancels pending tools and prevents further results.

Concurrency safety is a tool-declared property (``tool.is_concurrency_safe``);
the legacy static name set and the ``side_effect`` heuristic serve as fallbacks
for unmigrated tools. Progress is driven by ``asyncio.wait(FIRST_COMPLETED)``
so the first tool completion surfaces immediately rather than waiting for the
slowest task.

Usage::

    executor = StreamingExecutor(tools=deps.tools, tool_ctx=tool_ctx)
    executor.add_tool(tool_call_block, assistant_message)
    async for update in executor.get_remaining_results():
        process(update)
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, AsyncIterator

from prismclaw.engine.observability.feature_flags import (
    FLAG_SCHEDULER_V2,
    FLAG_STREAMING_V2,
    is_enabled,
)
from prismclaw.engine.foundation.metrics import emit

logger = logging.getLogger(__name__)

# Metric names follow v3 §11.1: {ns}.{comp}.{name}[_{unit}].
_METRIC_TOOL_SCHEDULED = "streaming.tool.scheduled"
_METRIC_TOOL_FIRST_COMPLETION_MS = "streaming.tool.first_completion_ms"
_METRIC_TOOL_QUEUE_WAIT_MS = "streaming.tool.queue_wait_ms"

# Legacy name-based fallback for read-only tools that have not declared
# ``is_concurrency_safe``. All current builtin read tools declare the
# attribute on the class; this set only remains as the fallback contract
# for unmigrated/external tools. New tools MUST declare
# ``is_concurrency_safe`` on the class instead of being added here.
CONCURRENCY_SAFE_TOOLS = frozenset({
    "read", "grep", "glob", "ls",
})


def _lane_label(is_concurrency_safe: bool) -> str:
    """Render the lane name for a tool call, used as a metric label.

    Kept as a module-level helper so the label set stays consistent
    across all emit sites and can be grep'd by tests.
    """
    return "safe" if is_concurrency_safe else "non_safe"


class ToolStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    DISCARDED = "discarded"


@dataclass
class ToolExecutionUpdate:
    """A single tool execution result update."""
    tool_call_id: str
    tool_name: str
    result: Any  # ToolResult
    status: ToolStatus
    is_concurrency_safe: bool = False


@dataclass
class TrackedTool:
    """Internal tracking structure for a tool being executed."""
    id: str
    block: dict[str, Any]
    status: ToolStatus = ToolStatus.PENDING
    is_concurrency_safe: bool = False
    task: asyncio.Task[Any] | None = None
    result: Any | None = None

    @property
    def tool_name(self) -> str:
        fn = (self.block.get("function") or {})
        return fn.get("name", "")


def _resolve_is_concurrency_safe(tool: Any, tool_name: str) -> bool:
    """Prefer tool-declared ``is_concurrency_safe``.

    Resolution order:
    1. Explicit class attribute ``is_concurrency_safe`` (bool) — authoritative.
    2. Legacy ``CONCURRENCY_SAFE_TOOLS`` static set — name-based fallback.
    3. ``side_effect`` attribute — ``False`` ⇒ assume safe (backward
       compat for tools that haven't been migrated yet; tools that
       are *not* actually safe despite ``side_effect=False`` MUST
       declare ``is_concurrency_safe = False``).
    4. Default to ``False`` (unsafe).
    """
    declared = getattr(tool, "is_concurrency_safe", None)
    if isinstance(declared, bool):
        return declared
    if tool_name and tool_name in CONCURRENCY_SAFE_TOOLS:
        return True
    # Backward compatibility: tools with side_effect=False
    # were previously treated as parallel-safe. Preserve that
    # behaviour unless the tool has explicitly opted out.
    side_effect = getattr(tool, "side_effect", None)
    if isinstance(side_effect, bool) and not side_effect:
        return True
    return False


class StreamingExecutor:
    """Streaming tool executor with concurrency control.

    Safe tools run in parallel up to ``safe_concurrency``, non-safe tools
    run exclusively through a FIFO lock. Results are yielded in add_tool
    order; the first completion surfaces immediately thanks to
    ``asyncio.wait(FIRST_COMPLETED)``.
    """

    def __init__(
        self,
        *,
        tools: Any,
        tool_ctx: Any,
        default_timeout_s: float = 120.0,
        safe_concurrency: int = 10,
    ) -> None:
        self._tools = tools
        self._tool_ctx = tool_ctx
        self._default_timeout_s = default_timeout_s
        # ``FLAG_SCHEDULER_V2`` defaults to True. Off bypasses both the
        # semaphore and the FIFO lock — legacy unrestricted behaviour,
        # kept as a rollback lever for staged rollout.
        scheduler_v2 = is_enabled(FLAG_SCHEDULER_V2, default=True)
        self._safe_semaphore: asyncio.Semaphore | None = (
            asyncio.Semaphore(max(1, int(safe_concurrency)))
            if scheduler_v2
            else None
        )
        self._non_safe_lock: asyncio.Lock | None = (
            asyncio.Lock() if scheduler_v2 else None
        )
        # ``FLAG_STREAMING_V2`` controls whether metrics are emitted
        # for the streaming path. The FIRST_COMPLETED scheduler itself
        # is unconditionally active — the flag is observability-only
        # so dashboards can be muted during staged rollout.
        self._streaming_v2 = is_enabled(FLAG_STREAMING_V2, default=True)

        self._tracked: list[TrackedTool] = []
        self._discarded = False
        # Zero point for the first-completion latency histogram.
        self._executor_start_monotonic = time.monotonic()

    def add_tool(
        self,
        block: dict[str, Any],
        assistant_message: Any | None = None,
    ) -> TrackedTool:
        """Add a tool call to be executed.

        The tool is queued for execution. Safe tools may start immediately
        (bounded by ``safe_concurrency``), non-safe tools wait for the
        FIFO non-safe lock.
        """
        tool_call_id = block.get("id", "")
        fn = (block.get("function") or {})
        tool_name = fn.get("name", "")

        tool = self._tools.get(tool_name) if hasattr(self._tools, "get") else None
        is_safe = _resolve_is_concurrency_safe(tool, tool_name)

        tracked = TrackedTool(
            id=tool_call_id,
            block=block,
            is_concurrency_safe=is_safe,
        )
        self._tracked.append(tracked)

        if not self._discarded:
            tracked.task = asyncio.create_task(
                self._execute_tool(tracked),
                name=f"tool_exec_{tool_call_id}",
            )
            if self._streaming_v2:
                emit(
                    _METRIC_TOOL_SCHEDULED,
                    1.0,
                    kind="counter",
                    labels={
                        "tool": tool_name or "unknown",
                        "lane": "safe" if is_safe else "non_safe",
                    },
                )

        return tracked

    async def get_completed_results(self) -> list[ToolExecutionUpdate]:
        """Get all completed results without waiting for pending ones."""
        results = []
        for tracked in self._tracked:
            if tracked.status == ToolStatus.COMPLETED and tracked.result is not None:
                results.append(ToolExecutionUpdate(
                    tool_call_id=tracked.id,
                    tool_name=tracked.tool_name,
                    result=tracked.result,
                    status=tracked.status,
                    is_concurrency_safe=tracked.is_concurrency_safe,
                ))
        return results

    async def get_remaining_results(self) -> AsyncIterator[ToolExecutionUpdate]:
        """Drain remaining tool execution results in add_tool order.

        Progress is driven by ``asyncio.wait(FIRST_COMPLETED)`` so the
        consumer sees each tool the instant it finishes, while still
        emitting in deterministic add_tool order.
        """
        # Phase 1: snapshot live tasks and their tracked indices.
        index_to_task: dict[int, asyncio.Task[Any]] = {}
        for idx, tracked in enumerate(self._tracked):
            if tracked.task is not None and not tracked.task.done():
                index_to_task[idx] = tracked.task

        emitted = 0
        next_idx = 0
        n = len(self._tracked)
        # ``first_completion_ms`` is recorded once per executor instance.
        first_completion_recorded = False

        while emitted < n:
            # Emit any in-order tracked that is already done.
            while next_idx < n:
                tracked = self._tracked[next_idx]
                if tracked.status in (
                    ToolStatus.COMPLETED,
                    ToolStatus.FAILED,
                    ToolStatus.DISCARDED,
                ) and (
                    tracked.result is not None
                    or tracked.status == ToolStatus.DISCARDED
                ):
                    emitted += 1
                    next_idx += 1
                    if tracked.status == ToolStatus.DISCARDED:
                        continue
                    if self._streaming_v2 and not first_completion_recorded:
                        elapsed_ms = (
                            time.monotonic() - self._executor_start_monotonic
                        ) * 1000.0
                        emit(
                            _METRIC_TOOL_FIRST_COMPLETION_MS,
                            elapsed_ms,
                            kind="histogram",
                            labels={"lane": _lane_label(tracked.is_concurrency_safe)},
                        )
                        first_completion_recorded = True
                    yield ToolExecutionUpdate(
                        tool_call_id=tracked.id,
                        tool_name=tracked.tool_name,
                        result=tracked.result,
                        status=tracked.status,
                        is_concurrency_safe=tracked.is_concurrency_safe,
                    )
                    continue
                break

            if not index_to_task:
                # Everything is accounted for; exit the loop.
                break

            # Phase 2: wait for at least one new completion, then iterate.
            done, _ = await asyncio.wait(
                list(index_to_task.values()),
                return_when=asyncio.FIRST_COMPLETED,
            )
            for completed in done:
                for idx, task in list(index_to_task.items()):
                    if task is completed:
                        del index_to_task[idx]
                        break

        # Defensive: if the consumer stopped early (e.g. break) and
        # tasks are still running, do not leave them to leak.
        if not self._discarded and index_to_task:
            for task in index_to_task.values():
                if not task.done():
                    task.cancel()

    def discard(self) -> None:
        """Cancel all pending tool executions and prevent further results."""
        self._discarded = True
        for tracked in self._tracked:
            if tracked.task is not None and not tracked.task.done():
                tracked.task.cancel()
            elif tracked.status == ToolStatus.PENDING:
                tracked.status = ToolStatus.DISCARDED
        # asyncio.Lock is cooperative: a holder cancelled mid-await
        # releases the lock automatically, so no explicit unlock is needed.

    @property
    def is_discarded(self) -> bool:
        return self._discarded

    @property
    def tracked_count(self) -> int:
        return len(self._tracked)

    @property
    def completed_count(self) -> int:
        return sum(1 for t in self._tracked if t.status == ToolStatus.COMPLETED)

    async def _execute_tool(self, tracked: TrackedTool) -> None:
        """Execute a single tool with concurrency control."""
        try:
            await self._run_tool(tracked)
        except asyncio.CancelledError:
            # Mark the tracked as DISCARDED so consumers (and metrics)
            # see a consistent terminal state instead of stuck PENDING/RUNNING.
            if tracked.status not in (
                ToolStatus.COMPLETED,
                ToolStatus.FAILED,
                ToolStatus.DISCARDED,
            ):
                tracked.status = ToolStatus.DISCARDED
            raise

    async def _run_tool(self, tracked: TrackedTool) -> None:
        if self._discarded:
            tracked.status = ToolStatus.DISCARDED
            return

        fn = (tracked.block.get("function") or {})
        tool_name = fn.get("name", "")
        tool_call_id = tracked.id

        # Separate concurrency lanes. When ``FLAG_SCHEDULER_V2`` is off both
        # primitives are ``None`` and we run unrestricted (legacy behaviour).
        if tracked.is_concurrency_safe and self._safe_semaphore is not None:
            await self._safe_semaphore.acquire()
        elif self._non_safe_lock is not None:
            await self._non_safe_lock.acquire()

        if self._discarded:
            if tracked.is_concurrency_safe and self._safe_semaphore is not None:
                self._safe_semaphore.release()
            elif self._non_safe_lock is not None:
                self._non_safe_lock.release()
            tracked.status = ToolStatus.DISCARDED
            return

        tracked.status = ToolStatus.RUNNING

        # 统一工具调用观测日志（与 execution_entry._dispatch_single /
        # _execute_subagent_with_failsoft 同格式，惰性导入避免模块环）。
        from prismclaw.engine.execution.dispatch.execution_entry import log_tool_call

        _t0 = time.monotonic()
        _end_status = "ok"
        _end_detail: str | None = None
        log_tool_call(
            "start",
            session_id=self._tool_ctx.session_id,
            name=tool_name or "<unknown>",
            call_id=tool_call_id,
        )

        # Record the per-call execution start (epoch ms) for the
        # trajectory tool-cell duration. ctx.tool_call_starts is a shared
        # dict — writes are visible to the runner task even if this runs
        # in a spawned task (copy_context() is a shallow copy).
        try:
            from prismclaw.engine.trajectory.bus_context import current_runtime
            _rt = current_runtime()
            if _rt is not None:
                _rt.tool_call_starts[tool_call_id] = int(time.time() * 1000)
        except Exception:
            pass

        try:
            tool = self._tools.get(tool_name) if hasattr(self._tools, "get") else None

            if tool is None:
                from prismclaw.schema.agent.data_types import TextBlock
                from prismclaw.schema.content.content_types import ToolResult
                tracked.result = ToolResult(
                    tool_call_id=tool_call_id,
                    content=[TextBlock(text=f"Unknown tool: {tool_name}")],
                    is_error=True,
                )
                tracked.status = ToolStatus.FAILED
                _end_status = "unknown_tool"
                return

            # Parse arguments.
            # ToolCallBuffer.finalize → ToolCallSlot.to_openai_dict guarantees
            # fn["arguments"] is a JSON string.  We delegate to _function_args
            # because that is the ONLY function allowed to interpret the
            # arguments field; re-implementing parsing logic here was a source
            # of drift (same input, different recovery outcome depending on
            # which execution path was taken).
            from prismclaw.engine.execution.dispatch.execution_entry import _error, _function_args

            call_shape = {"function": fn}
            arguments = _function_args(call_shape)

            # Short-circuit unrecoverable argument corruption.  Mirror the
            # logic in _dispatch_single: passing `{"_raw": …}` to tools makes
            # them return generic field-missing errors that trigger retry
            # loops.  We surface the root cause immediately.
            if isinstance(arguments, dict) and set(arguments.keys()) == {"_raw"}:
                raw = arguments["_raw"]
                n = len(raw) if isinstance(raw, (str, bytes)) else -1
                preview = raw[:200] if isinstance(raw, str) else repr(raw)[:200]
                msg = (
                    f"工具 {tool_name!r} 的参数是格式错误的 JSON"
                    f"（共 {n} 字节，以下为前 {min(n, 200)} 字节）：\n```\n{preview}\n```\n"
                    "请以合法的 JSON 对象重新传入 'arguments'——"
                    "重复调用这一完全相同的调用必然失败。"
                )
                tracked.result = _error(tool_call_id, msg)
                tracked.status = ToolStatus.FAILED
                _end_status = "bad_args"
                return

            # Execute
            try:
                # 子代理（sub_agent）拉起整条嵌套 agent 循环，与单次真实工具调用
                # 语义/量级不同，不复用通用默认时限：优先用其自身解析（按 manifest
                # 上限），否则退回通用默认。
                _dispatch_timeout_s = self._default_timeout_s
                _resolver = getattr(tool, "resolve_dispatch_timeout_seconds", None)
                if callable(_resolver):
                    try:
                        _dispatch_timeout_s = float(_resolver(arguments))
                    except Exception:
                        _dispatch_timeout_s = self._default_timeout_s
                # timeout <= 0 表示「无墙钟上限」（fork 子代理），asyncio.wait_for
                # 的 timeout=0 会立即超时，必须转为 None（仅受 abort 约束）。
                _wait_for_timeout = _dispatch_timeout_s if _dispatch_timeout_s > 0 else None
                result = await asyncio.wait_for(
                    tool.execute(arguments, self._tool_ctx),
                    timeout=_wait_for_timeout,
                )
                tracked.result = result
                tracked.status = ToolStatus.COMPLETED
                _end_status = "error" if getattr(result, "is_error", False) else "ok"
            except asyncio.TimeoutError:
                from prismclaw.schema.agent.data_types import TextBlock
                from prismclaw.schema.content.content_types import ToolResult
                tracked.result = ToolResult(
                    tool_call_id=tool_call_id,
                    content=[TextBlock(text=f"Tool '{tool_name}' timed out after {self._default_timeout_s}s")],
                    is_error=True,
                )
                tracked.status = ToolStatus.FAILED
                _end_status = "timeout"
                _end_detail = f"limit={_dispatch_timeout_s}s"
            except Exception as exc:
                from prismclaw.schema.shared import MCPServerDeadError
                from prismclaw.schema.agent.data_types import TextBlock
                from prismclaw.schema.content.content_types import ToolResult

                if isinstance(exc, MCPServerDeadError):
                    tm = self._tool_ctx.task_manager
                    handler = self._tool_ctx.mcp_death_handler
                    if tm is not None and handler is not None:
                        try:
                            tm.spawn(
                                name=f"mcp:death:{exc.server_name}",
                                coro=handler(exc.server_name),
                                category="mcp",
                            )
                        except Exception as spawn_exc:
                            logger.warning(
                                "mcp.death_handler_skipped name=%s reason=%s",
                                exc.server_name,
                                type(spawn_exc).__name__,
                            )
                    tracked.result = ToolResult(
                        tool_call_id=tool_call_id,
                        content=[
                            TextBlock(
                                text=(
                                    f"MCP 服务 {exc.server_name!r} 当前不可用，"
                                    f"已从本次对话中移除。如需重试，请使用 /mcp restart {exc.server_name}。"
                                )
                            )
                        ],
                        is_error=True,
                    )
                    tracked.status = ToolStatus.FAILED
                    _end_status = "mcp_dead"
                    _end_detail = exc.server_name
                    return
                tracked.result = ToolResult(
                    tool_call_id=tool_call_id,
                    content=[TextBlock(text=f"工具 '{tool_name}' 执行出错：{exc}")],
                    is_error=True,
                )
                tracked.status = ToolStatus.FAILED
                _end_status = "exception"
                _end_detail = type(exc).__name__

        finally:
            log_tool_call(
                "end",
                session_id=self._tool_ctx.session_id,
                name=tool_name or "<unknown>",
                call_id=tool_call_id,
                status=_end_status,
                duration_ms=int((time.monotonic() - _t0) * 1000),
                detail=_end_detail,
            )
            if tracked.is_concurrency_safe and self._safe_semaphore is not None:
                self._safe_semaphore.release()
            elif self._non_safe_lock is not None:
                self._non_safe_lock.release()
