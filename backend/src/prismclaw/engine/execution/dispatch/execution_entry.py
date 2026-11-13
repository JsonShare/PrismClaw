from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC
from typing import Any

from prismclaw.engine.execution.dispatch.streaming_executor import (
    _resolve_is_concurrency_safe,
)
from prismclaw.engine.toolkit.registry.registry import ToolRegistry
from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext
from prismclaw.schema.agent.data_types import TextBlock
from prismclaw.schema.content.content_types import ToolResult

logger = logging.getLogger(__name__)

_SUBAGENT_TOOL_NAME = "sub_agent"
_SUBAGENT_PARALLEL_DEFAULT_LIMIT = 5


def log_tool_call(
    phase: str,
    *,
    session_id: str | None,
    name: str,
    call_id: str,
    status: str | None = None,
    duration_ms: int | None = None,
    detail: str | None = None,
) -> None:
    """统一工具调用观测日志（start/end 各一条，INFO 级）。

    覆盖全部三条执行路径：
    _dispatch_single（主循环 sequential/parallel 桶）、
    _execute_subagent_with_failsoft（subagent_parallel 桶）、
    streaming_executor._run_tool（流式执行路径）。
    start/end 配对可用于定位挂死调用（有 start 无 end）。
    """
    parts = [
        f"tool_call_{phase}",
        f"session_id={session_id}",
        f"tool={name}",
        f"call_id={call_id}",
    ]
    if status is not None:
        parts.append(f"status={status}")
    if duration_ms is not None:
        parts.append(f"duration_ms={duration_ms}")
    if detail:
        parts.append(f"detail={detail}")
    logger.info(" ".join(parts))


def _result_text(result: ToolResult) -> str:
    """提取 ToolResult 的文本内容（POST_TOOL hook 载荷）。"""
    parts: list[str] = []
    for block in result.content:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts)


async def _apply_post_tool_hooks(
    context: ToolContext,
    tool_calls: list[dict[str, Any]],
    results: list[ToolResult],
) -> None:
    """逐调用分发 POST_TOOL hooks，消费 ``updated_mcp_tool_output`` 改写结果。

    派发点在 ``execute_tool_calls`` 末尾（主循环与子代理循环的公共汇聚层）：
    插件 hooks.json 的 PostToolUse matcher 才能命中子代理内部执行的 MCP 工具
    （主循环 after_tool_call 是批次级且 tool_name 恒 None，触不到子代理链路）。

    语义：
    - hook 返回的 ``updated_mcp_tool_output`` 为字符串时改写对应 ToolResult 的
      文本内容（保留原 call_id/错误位/artifact）；非字符串（dict 等）以 JSON
      序列化为文本；
    - hook 失败/超时只记日志不改写（non_blocking 语义，与参考实现一致）；
    - 无 hook_dispatcher 或无已注册 POST_TOOL handler 时零开销通过。
    """
    dispatcher = getattr(context, "hook_dispatcher", None)
    if dispatcher is None:
        return
    from prismclaw.engine.hooks import HookContext, HookEvent, _log_hook_outcome

    if not getattr(dispatcher, "_hooks", {}).get(HookEvent.POST_TOOL):
        return

    for idx, (call, result) in enumerate(zip(tool_calls, results, strict=True)):
        name = _function_name(call)
        if not name or getattr(result, "is_error", False):
            continue
        hook_context = HookContext(
            event=HookEvent.POST_TOOL.value,
            session_id=context.session_id,
            cwd=str(context.workspace_path),
            tool_name=name,
            tool_input=_function_args(call),
            tool_output=_result_text(result),
        )
        try:
            hook_result = await dispatcher.dispatch(HookEvent.POST_TOOL, hook_context)
        except Exception:
            logger.warning(
                "post_tool_hook.dispatch_failed tool=%s", name, exc_info=True,
            )
            continue
        _log_hook_outcome(HookEvent.POST_TOOL, hook_result)
        rewritten = getattr(hook_result, "updated_mcp_tool_output", None)
        if rewritten is None:
            continue
        if isinstance(rewritten, str):
            new_text = rewritten
        else:
            try:
                new_text = json.dumps(rewritten, ensure_ascii=False)
            except (TypeError, ValueError):
                logger.warning(
                    "post_tool_hook.rewrite_not_serializable tool=%s", name,
                )
                continue
        if new_text == _result_text(result):
            continue
        results[idx] = ToolResult(
            tool_call_id=result.tool_call_id,
            content=[TextBlock(text=new_text)],
            is_error=result.is_error,
            ui_artifact=result.ui_artifact,
            persistence_artifact=result.persistence_artifact,
            state_delta=result.state_delta,
            runtime_control=result.runtime_control,
            subagent_envelope=result.subagent_envelope,
            reference_artifact=result.reference_artifact,
            metadata=result.metadata,
        )


def _run_tool_validate(tool: Any, args: dict[str, Any], context: ToolContext) -> str | None:
    """Call a tool's validate() hook if it exists.

    Returns None if validation passes or the tool has no validate method.
    Returns an error message string if validation fails.
    """
    validate_fn = getattr(tool, "validate", None)
    if not callable(validate_fn):
        return None
    try:
        result = validate_fn(args, context)
        if result is None:
            return None
        return str(result)
    except Exception as exc:
        return f"Validation error for tool '{tool.name}': {exc}"


@dataclass
class SubagentParallelDispatchPlan:
    requested_n: int
    accepted: list[tuple[int, dict[str, Any]]]
    rejected: list[tuple[int, dict[str, Any]]]
    max_parallel_per_turn: int
    batch_id: str | None
    handoff_extras_by_call_id: dict[str, dict[str, Any]]


@dataclass
class PreparedSubagentCall:
    index: int
    call: dict[str, Any]
    context: ToolContext


@dataclass
class SubagentParallelRuntime:
    requested_n: int
    prepared_calls: list[PreparedSubagentCall]
    rejected: list[tuple[int, dict[str, Any]]]
    max_parallel_per_turn: int
    progress_batch_id: str | None


def _tool_timeout_seconds(tool: Any, default_s: float) -> float:
    override = getattr(tool, "timeout_seconds", None)
    if override is None:
        return default_s
    try:
        value = float(override)
    except (TypeError, ValueError):
        return default_s
    return value if value > 0 else 0.0


def _resolve_dispatch_timeout(tool: Any, args: Any, default_s: float) -> float:
    """子代理分发超时：优先用 SubAgentTool 自身的解析（按 manifest 上限），
    其余工具退回通用 ``tool_seconds``。仅 SubAgentTool 暴露
    ``resolve_dispatch_timeout_seconds``，故对非子代理工具无副作用。
    """
    resolver = getattr(tool, "resolve_dispatch_timeout_seconds", None)
    if callable(resolver):
        try:
            return float(resolver(args))
        except Exception:
            logger.warning("subagent.timeout_resolve_failed", exc_info=True)
    return _tool_timeout_seconds(tool, default_s)


async def execute_tool_calls(
    registry: ToolRegistry,
    tool_calls: list[dict[str, Any]],
    context: ToolContext,
    *,
    default_tool_timeout_s: float = 120.0,
    subagent_max_parallel_per_turn: int = _SUBAGENT_PARALLEL_DEFAULT_LIMIT,
) -> list[ToolResult]:
    parallel: list[tuple[int, dict[str, Any]]] = []
    sequential: list[tuple[int, dict[str, Any]]] = []
    subagent_parallel: list[tuple[int, dict[str, Any]]] = []

    for i, call in enumerate(tool_calls):
        name = _function_name(call)
        tool = registry.get(name) if name else None
        if name == _SUBAGENT_TOOL_NAME and tool is not None:
            subagent_parallel.append((i, call))
        elif tool is None or not _resolve_is_concurrency_safe(tool, name):
            sequential.append((i, call))
        else:
            parallel.append((i, call))

    results: list[ToolResult | None] = [None] * len(tool_calls)

    async def run_one(
        i: int, call: dict[str, Any], ctx_override: ToolContext | None = None
    ) -> None:
        results[i] = await _dispatch_single(
            registry,
            call,
            ctx_override or context,
            default_timeout_s=default_tool_timeout_s,
        )

    if parallel:
        await _run_parallel_fail_soft(
            parallel,
            run_one,
            results,
            registry=registry,
            context=context,
            default_tool_timeout_s=default_tool_timeout_s,
        )
    for i, call in sequential:
        await run_one(i, call)

    if subagent_parallel:
        subagent_plan = plan_subagent_parallel_dispatch(
            subagent_parallel,
            parent_context=context,
            max_parallel_per_turn=subagent_max_parallel_per_turn,
        )
        await _run_subagent_parallel_bucket(
            subagent_plan,
            registry,
            context,
            results,
            default_tool_timeout_s=default_tool_timeout_s,
        )

    final: list[ToolResult] = []
    for r in results:
        assert r is not None
        final.append(r)

    # POST_TOOL hooks：逐调用派发（主循环与子代理循环的公共汇聚点），
    # 消费插件 PostToolUse 钩子返回的 updated_mcp_tool_output 改写结果。
    await _apply_post_tool_hooks(context, tool_calls, final)
    return final


async def execute_preflighted_tool_calls(
    registry: ToolRegistry,
    tool_calls: list[dict[str, Any]],
    context: ToolContext,
    *,
    approved_calls: list[dict[str, Any]] | None = None,
    denied_call_ids: dict[str, str] | None = None,
    default_tool_timeout_s: float = 120.0,
    subagent_max_parallel_per_turn: int = _SUBAGENT_PARALLEL_DEFAULT_LIMIT,
) -> list[ToolResult]:
    results: list[ToolResult | None] = [None] * len(tool_calls)

    _denied_call_ids = denied_call_ids or {}
    _approved_calls = approved_calls or tool_calls

    for idx, call in enumerate(tool_calls):
        call_id = call.get("id", "")
        if call_id in _denied_call_ids:
            results[idx] = _error(call_id, _denied_call_ids[call_id])

    approved_call_tokens = {id(call) for call in _approved_calls}
    approved_slots = [
        (idx, call)
        for idx, call in enumerate(tool_calls)
        if id(call) in approved_call_tokens
    ]

    if approved_slots:
        executed = await execute_tool_calls(
            registry,
            [call for _, call in approved_slots],
            context,
            default_tool_timeout_s=default_tool_timeout_s,
            subagent_max_parallel_per_turn=subagent_max_parallel_per_turn,
        )
        for (idx, _call), result in zip(approved_slots, executed, strict=True):
            results[idx] = result

    final: list[ToolResult] = []
    for idx, result in enumerate(results):
        if result is None:
            call_id = tool_calls[idx].get("id", "")
            result = _error(call_id, "内部错误：缺少派发结果")
        final.append(result)
    return final


async def _run_subagent_parallel_bucket(
    subagent_plan: SubagentParallelDispatchPlan,
    registry: ToolRegistry,
    parent_context: ToolContext,
    results: list[ToolResult | None],
    *,
    default_tool_timeout_s: float,
) -> None:
    async with _subagent_progress_emitter_scope(
        parent_context=parent_context,
        batch_id=subagent_plan.batch_id,
    ) as emitter:
        runtime = prepare_subagent_parallel_runtime(
            subagent_plan,
            parent_context=parent_context,
            progress_emitter=emitter,
        )
        _apply_subagent_rate_limit_results(runtime=runtime, results=results)
        if runtime.rejected:
            _emit_fanout_rate_limited_audit(
                parent_session_id=parent_context.session_id,
                requested_n=runtime.requested_n,
                accepted_n=len(runtime.prepared_calls),
                rejected_n=len(runtime.rejected),
                progress_batch_id=runtime.progress_batch_id,
            )
        if not runtime.prepared_calls:
            return

        semaphore = asyncio.Semaphore(max(1, len(runtime.prepared_calls)))

        async def _sem_execute(prepared_call: PreparedSubagentCall) -> ToolResult:
            async with semaphore:
                return await _execute_subagent_with_failsoft(
                    registry,
                    prepared_call.call,
                    prepared_call.context,
                    default_timeout_s=default_tool_timeout_s,
                )

        gathered = await asyncio.gather(
            *[_sem_execute(prepared_call) for prepared_call in runtime.prepared_calls]
        )
        for prepared_call, tool_result in zip(
            runtime.prepared_calls,
            gathered,
            strict=True,
        ):
            results[prepared_call.index] = tool_result


def plan_subagent_parallel_dispatch(
    subagent_parallel: list[tuple[int, dict[str, Any]]],
    *,
    parent_context: ToolContext,
    max_parallel_per_turn: int,
) -> SubagentParallelDispatchPlan:
    import uuid as _uuid

    requested_n = len(subagent_parallel)
    accepted = subagent_parallel[:max_parallel_per_turn]
    rejected = subagent_parallel[max_parallel_per_turn:]
    handoff_extras_by_call_id = (
        parent_context.pending_handoff_dispatch_extras or {}
    )
    has_accepted_chain_dispatch = any(
        call.get("id") in handoff_extras_by_call_id for _, call in accepted
    )
    batch_id: str | None = None
    if len(accepted) >= 2 or has_accepted_chain_dispatch:
        batch_id = _uuid.uuid4().hex
    return SubagentParallelDispatchPlan(
        requested_n=requested_n,
        accepted=accepted,
        rejected=rejected,
        max_parallel_per_turn=max_parallel_per_turn,
        batch_id=batch_id,
        handoff_extras_by_call_id=handoff_extras_by_call_id,
    )


def prepare_subagent_parallel_runtime(
    subagent_plan: SubagentParallelDispatchPlan,
    *,
    parent_context: ToolContext,
    progress_emitter: Any | None,
) -> SubagentParallelRuntime:
    prepared_calls = [
        PreparedSubagentCall(
            index=index,
            call=call,
            context=_build_subagent_call_context(
                parent_context=parent_context,
                call=call,
                batch_id=subagent_plan.batch_id,
                handoff_extras_by_call_id=subagent_plan.handoff_extras_by_call_id,
                progress_emitter=progress_emitter,
            ),
        )
        for index, call in subagent_plan.accepted
    ]
    return SubagentParallelRuntime(
        requested_n=subagent_plan.requested_n,
        prepared_calls=prepared_calls,
        rejected=subagent_plan.rejected,
        max_parallel_per_turn=subagent_plan.max_parallel_per_turn,
        progress_batch_id=subagent_plan.batch_id,
    )


def _apply_subagent_rate_limit_results(
    *,
    runtime: SubagentParallelRuntime,
    results: list[ToolResult | None],
) -> None:
    rejected_n = len(runtime.rejected)
    for idx, call in runtime.rejected:
        results[idx] = _subagent_rate_limited_result(
            call_id=call.get("id", ""),
            max_parallel_per_turn=runtime.max_parallel_per_turn,
            rejected_n=rejected_n,
        )


def _subagent_rate_limited_result(
    *,
    call_id: str,
    max_parallel_per_turn: int,
    rejected_n: int,
) -> ToolResult:
    return ToolResult(
        tool_call_id=call_id,
        content=[
            TextBlock(
                text=(
                    f"[子代理限流] 每轮子代理并发派发上限为 "
                    f"{max_parallel_per_turn} 个；请在后续轮次中提交剩余的 "
                    f"{rejected_n} 个调用"
                )
            )
        ],
        is_error=True,
    )


def _build_subagent_call_context(
    *,
    parent_context: ToolContext,
    call: dict[str, Any],
    batch_id: str | None,
    handoff_extras_by_call_id: dict[str, dict[str, Any]],
    progress_emitter: Any | None,
) -> ToolContext:
    from dataclasses import replace as _replace

    if batch_id is None and not handoff_extras_by_call_id:
        return parent_context
    updates: dict[str, Any] = {}
    if batch_id is not None:
        updates["progress_batch_id"] = batch_id
        if progress_emitter is not None:
            updates["subagent_progress_emitter"] = progress_emitter
    return _replace(parent_context, **updates)


@contextlib.asynccontextmanager
async def _subagent_progress_emitter_scope(
    *,
    parent_context: ToolContext,
    batch_id: str | None,
) -> AsyncIterator[Any | None]:
    if batch_id is None:
        yield None
        return

    from prismclaw.engine.toolkit.subagent.subagent_progress_emitter import (
        SubagentProgressEmitter,
    )

    sink = parent_context.subagent_progress_sink
    emitter = SubagentProgressEmitter(progress_batch_id=batch_id, sink=sink)
    flush_task = asyncio.create_task(emitter._flush_loop())
    try:
        yield emitter
    finally:
        try:
            await emitter.flush_final()
        except Exception:
            logger.warning("subagent.progress.flush_final_failed", exc_info=True)
        flush_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await flush_task


async def _execute_subagent_with_failsoft(
    registry: ToolRegistry,
    call: dict[str, Any],
    context: ToolContext,
    *,
    default_timeout_s: float,
) -> ToolResult:
    from prismclaw.engine.foundation.runtime_util import (
        AgentAbortedError,
        AgentTimeoutError,
        run_with_timeout,
    )

    call_id = call.get("id", "")
    name = _function_name(call) or "sub_agent"
    args = _function_args(call)
    t0 = time.monotonic()

    def _log_end(status: str, detail: str | None = None) -> None:
        log_tool_call(
            "end",
            session_id=context.session_id,
            name=name,
            call_id=call_id,
            status=status,
            duration_ms=int((time.monotonic() - t0) * 1000),
            detail=detail,
        )

    log_tool_call("start", session_id=context.session_id, name=name, call_id=call_id)

    tool = registry.get(name)
    if tool is None:
        _log_end("not_registered")
        return _error(call_id, f"工具 {name!r} 未注册")

    tool_timeout_s = _resolve_dispatch_timeout(tool, args, default_timeout_s)

    try:
        result = await run_with_timeout(
            tool.execute(args, context),
            timeout_s=tool_timeout_s,
            abort_event=context.abort,
            kind="tool",
        )
    except (AgentAbortedError, asyncio.CancelledError):
        _log_end("aborted")
        return ToolResult(
            tool_call_id=call_id,
            content=[TextBlock(text=f"[子代理已被父级中止] {name}")],
            is_error=True,
        )
    except AgentTimeoutError as te:
        _log_end("timeout", f"limit={te.limit_seconds}s")
        return ToolResult(
            tool_call_id=call_id,
            content=[
                TextBlock(
                    text=f"[子代理执行失败：TimeoutError] {name} 在 {te.limit_seconds}s 后超时"
                )
            ],
            is_error=True,
        )
    except KeyboardInterrupt:
        _log_end("interrupted")
        raise
    except SystemExit:
        _log_end("interrupted")
        raise
    except BaseException as exc:
        _log_end("exception", type(exc).__name__)
        return ToolResult(
            tool_call_id=call_id,
            content=[TextBlock(text=f"[子代理执行失败：{type(exc).__name__}] {exc}")],
            is_error=True,
        )

    _log_end("error" if getattr(result, "is_error", False) else "ok")

    # Same transport-id normalization as ``_dispatch_single`` (see comment
    # there): the sub-agent tool builds its result from an args-supplied id
    # that is not injected on this path either.
    if isinstance(result, ToolResult) and result.tool_call_id != call_id:
        result.tool_call_id = call_id
    return result


def _emit_fanout_rate_limited_audit(
    *,
    parent_session_id: str,
    requested_n: int,
    accepted_n: int,
    rejected_n: int,
    progress_batch_id: str | None,
) -> None:
    from datetime import datetime

    audit_logger = logging.getLogger("prismclaw.audit.subagent")
    payload: dict[str, Any] = {
        "event": "subagent_fanout_rate_limited",
        "ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "parent_session_id": parent_session_id,
        "requested_n": requested_n,
        "accepted_n": accepted_n,
        "rejected_n": rejected_n,
    }
    if progress_batch_id is not None:
        payload["progress_batch_id"] = progress_batch_id
    try:
        audit_logger.info(json.dumps(payload, separators=(",", ":"), sort_keys=False))
    except Exception:
        logger.warning("subagent.fanout_audit.emit_failed", exc_info=True)


async def _dispatch_single(
    registry: ToolRegistry,
    call: dict[str, Any],
    context: ToolContext,
    *,
    default_timeout_s: float = 120.0,
) -> ToolResult:
    from prismclaw.engine.foundation.runtime_util import (
        AgentAbortedError,
        AgentTimeoutError,
        run_with_timeout,
    )
    from prismclaw.schema.shared import MCPServerDeadError

    call_id = call.get("id", "")
    name = _function_name(call)
    args = _function_args(call)
    t0 = time.monotonic()

    def _log_end(status: str, detail: str | None = None) -> None:
        log_tool_call(
            "end",
            session_id=context.session_id,
            name=name or "<unknown>",
            call_id=call_id,
            status=status,
            duration_ms=int((time.monotonic() - t0) * 1000),
            detail=detail,
        )

    log_tool_call("start", session_id=context.session_id, name=name or "<unknown>", call_id=call_id)

    if not name:
        _log_end("no_name")
        return _error(call_id, "工具调用缺少函数名")

    tool = registry.get(name)
    if tool is None:
        _log_end("not_registered")
        return _error(call_id, f"工具 {name!r} 未注册")

    # Pre-execution validation: if the tool defines a validate() method,
    # run it before execute(). This lets tools reject
    # hallucinated/fabricated arguments (e.g., non-existent skill names)
    # before any side effects occur, returning immediate feedback to the LLM.
    validate_err = _run_tool_validate(tool, args, context)
    if validate_err is not None:
        _log_end("invalid_args")
        return _error(call_id, validate_err)

    tool_timeout_s = _resolve_dispatch_timeout(tool, args, default_timeout_s)

    try:
        result = await run_with_timeout(
            tool.execute(args, context),
            timeout_s=tool_timeout_s,
            abort_event=context.abort,
            kind="tool",
        )
    except AgentTimeoutError as te:
        _log_end("timeout", f"limit={te.limit_seconds}s")
        return _error(call_id, f"{name} 在 {te.limit_seconds}s 后超时")
    except AgentAbortedError:
        _log_end("aborted")
        return _error(call_id, f"{name} 已中止")
    except MCPServerDeadError as dead:
        tm = context.task_manager
        handler = context.mcp_death_handler
        if tm is not None and handler is not None:
            try:
                tm.spawn(
                    name=f"mcp:death:{dead.server_name}",
                    coro=handler(dead.server_name),
                    category="mcp",
                )
            except Exception as spawn_exc:
                logger.warning(
                    "mcp.death_handler_skipped name=%s reason=%s",
                    dead.server_name,
                    type(spawn_exc).__name__,
                )
        _log_end("mcp_dead", dead.server_name)
        return _error(
            call_id,
            f"MCP 服务 {dead.server_name!r} 当前不可用，"
            f"已从本次对话中移除。如需重试，请使用 /mcp restart {dead.server_name}。",
        )
    except Exception as exc:
        _log_end("exception", type(exc).__name__)
        return _error(call_id, f"{name} 抛出 {type(exc).__name__}：{exc}")

    _log_end("error" if getattr(result, "is_error", False) else "ok")

    # Apply output_bound truncation for non-error results
    if isinstance(result, ToolResult) and not result.is_error:
        from pathlib import Path as _Path

        from prismclaw.engine.execution.output.output_bound import OutputBoundConfig, output_bound

        text_content = ""
        for block in result.content:
            if hasattr(block, "text"):
                text_content += block.text

        if text_content:
            spill_dir = _Path(context.workspace_path) / ".work" / "spills"
            config = OutputBoundConfig(spill_dir=spill_dir)
            truncated_text, extra_meta = output_bound(text_content, config=config, tool_name=name)

            if truncated_text != text_content:
                new_blocks = []
                for block in result.content:
                    if hasattr(block, "text"):
                        new_blocks.append(TextBlock(text=truncated_text))
                    else:
                        new_blocks.append(block)

                merged_meta = {**extra_meta}
                if result.metadata:
                    merged_meta.update(result.metadata)

                result = ToolResult(
                    tool_call_id=result.tool_call_id,
                    content=new_blocks,
                    is_error=result.is_error,
                    ui_artifact=result.ui_artifact,
                    persistence_artifact=result.persistence_artifact,
                    state_delta=result.state_delta,
                    runtime_control=result.runtime_control,
                    subagent_envelope=result.subagent_envelope,
                    reference_artifact=result.reference_artifact,
                    metadata=merged_meta,
                )

    # The transport call id is owned by the dispatcher, not by individual
    # tools: normalize it on every successful result so downstream consumers
    # (evidence builders, tree projection, compaction) can correlate results
    # with their calls regardless of the execution lane. The streaming lane
    # already restamps the id when draining results; the sequential and
    # legacy-parallel lanes converge here.
    if isinstance(result, ToolResult) and result.tool_call_id != call_id:
        result.tool_call_id = call_id
    return result


def _function_name(call: dict[str, Any]) -> str | None:
    fn = call.get("function") or {}
    return fn.get("name") if isinstance(fn, dict) else None


def repair_json_args(raw: str) -> dict[str, Any] | None:
    """Attempt to recover a valid argument dict from a malformed JSON string.

    Single salvage point for tool-call arguments.  Delegates to
    :func:`_salvage_json_object` (the repo's only JSON-recovery
    implementation) so there is exactly one heuristic set in the codebase.

    Returns the recovered dict, or ``None`` when the payload is
    unrecoverable.
    """
    from prismclaw.schema.llm.tool_call_types import _salvage_json_object

    return _salvage_json_object(raw)


def _function_args(call: dict[str, Any]) -> dict[str, Any]:
    """Parse a tool call's ``function.arguments`` string into a dict.

    Single parsing entry point for tool execution.

    Pipeline:
    1. ``json.loads`` on the arguments string.
    2. :func:`repair_json_args` to recover a valid dict from a malformed
       payload (truncation, duplicated JSON, dropped closing brace).
    3. ``{}`` when nothing else works — the tool's ``validate()`` /
       ``execute()`` surfaces the missing-field error.

    No ``_raw`` wrapper: the previous wrap-then-check indirection has been
    collapsed into this single function.
    """
    fn = call.get("function") or {}
    args = fn.get("arguments") if isinstance(fn, dict) else None
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, dict):
            return parsed
        # Non-dict top-level value or parse failure — attempt salvage.
        recovered = repair_json_args(args)
        if recovered is not None:
            return recovered
        # Unrecoverable: empty dict so tool validation reports missing fields.
        return {}
    if isinstance(args, dict):
        return args
    return {}


def _error(tool_call_id: str, message: str) -> ToolResult:
    return ToolResult(
        tool_call_id=tool_call_id,
        content=[TextBlock(text=message)],
        is_error=True,
    )


async def _run_parallel_fail_soft(
    parallel: list[tuple[int, dict[str, Any]]],
    run_one: Callable[[int, dict[str, Any], ToolContext | None], Awaitable[None]],
    results: list[ToolResult | None],
    *,
    registry: ToolRegistry | None = None,
    context: ToolContext | None = None,
    default_tool_timeout_s: float = 120.0,
) -> None:
    if not parallel:
        return

    from prismclaw.engine.observability.feature_flags import (
        FLAG_STREAMING_V2,
        is_enabled,
    )

    use_streaming = (
        registry is not None
        and context is not None
        and is_enabled(FLAG_STREAMING_V2, default=True)
    )

    if not use_streaming:
        await _run_parallel_fail_soft_gather(parallel, run_one, results)
        return

    streaming_batch: list[tuple[int, dict[str, Any]]] = []
    legacy_batch: list[tuple[int, dict[str, Any]]] = []
    for i, c in parallel:
        if _needs_legacy_parallel_path(c, registry, default_tool_timeout_s):
            legacy_batch.append((i, c))
        else:
            streaming_batch.append((i, c))

    if legacy_batch:
        await _run_parallel_fail_soft_gather(legacy_batch, run_one, results)

    if not streaming_batch:
        return

    from prismclaw.engine.execution.dispatch.streaming_executor import (
        StreamingExecutor,
    )

    executor = StreamingExecutor(
        tools=registry,
        tool_ctx=context,
        default_timeout_s=default_tool_timeout_s,
    )
    slot_to_call_id: dict[str, int] = {}
    slot_to_original_id: dict[int, str] = {}
    for i, c in streaming_batch:
        synthetic_id = f"__streaming_slot_{i}_{id(c)}"
        slot_to_call_id[synthetic_id] = i
        slot_to_original_id[i] = c.get("id", "")
        block = {**c, "id": synthetic_id}
        executor.add_tool(block)

    try:
        abort_task: asyncio.Task | None = None
        if context is not None and getattr(context, "abort", None) is not None:

            async def _discard_on_abort() -> None:
                try:
                    await context.abort.wait()
                except Exception:
                    pass
                executor.discard()

            abort_task = asyncio.create_task(_discard_on_abort())
        try:
            async for update in executor.get_remaining_results():
                slot_i = slot_to_call_id.get(update.tool_call_id)
                if slot_i is None:
                    logger.warning(
                        "tool.streaming_executor.unknown_call_id id=%s name=%s",
                        update.tool_call_id,
                        update.tool_name,
                    )
                    continue
                if update.result is not None:
                    update.result.tool_call_id = slot_to_original_id.get(slot_i, "")
                results[slot_i] = update.result
        finally:
            if abort_task is not None:
                if not abort_task.done():
                    abort_task.cancel()
                try:
                    await abort_task
                except (asyncio.CancelledError, BaseException):
                    pass
    except Exception as exc:
        logger.warning(
            "tool.streaming_executor.iterator_failed exc=%r",
            exc,
            exc_info=True,
        )
        for i, c in streaming_batch:
            if results[i] is None:
                name = _function_name(c) or "<unknown>"
                call_id = c.get("id", "")
                results[i] = _error(
                    call_id,
                    f"[streaming-fallback] {name} failed: {exc!r}",
                )


def _needs_legacy_parallel_path(
    call: dict[str, Any],
    registry: ToolRegistry,
    default_tool_timeout_s: float,
) -> bool:
    if bool(call.get("_needs_ctx_override", False)):
        return True
    if default_tool_timeout_s <= 0:
        return True

    name = _function_name(call)
    if name:
        tool = registry.get(name)
        if tool is not None and getattr(tool, "timeout_seconds", None) is not None:
            try:
                override = float(tool.timeout_seconds)
            except (TypeError, ValueError):
                override = None
            if override is not None and override != default_tool_timeout_s:
                return True

    return False


async def _run_parallel_fail_soft_gather(
    parallel: list[tuple[int, dict[str, Any]]],
    run_one: Callable[[int, dict[str, Any], ToolContext | None], Awaitable[None]],
    results: list[ToolResult | None],
) -> None:
    if not parallel:
        return
    outcomes = await asyncio.gather(
        *(run_one(i, c) for i, c in parallel),
        return_exceptions=True,
    )
    for (i, call), outcome in zip(parallel, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            name = _function_name(call) or "<unknown>"
            call_id = call.get("id", "")
            logger.warning(
                "tool.parallel_fail_soft idx=%s name=%s exc=%r",
                i,
                name,
                outcome,
            )
            results[i] = _error(
                call_id,
                f"[fail-soft] {name} failed: {outcome!r}",
            )
