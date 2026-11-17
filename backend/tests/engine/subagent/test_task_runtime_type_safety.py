"""ToolContext 运行时容器契约：``task_runtime`` 恒为 ``ParallelTaskExtras``。

engine 层用 ``ParallelTaskExtras`` 持有 parallel_task_store；
schema 层的 ``TaskRuntimeState`` 是持久化快照，由循环上下文（``ctx.task_runtime``）
持有，不得写入 ``ToolContext.task_runtime``。
"""
from __future__ import annotations

from pathlib import Path

from prismclaw.engine.security.gate.types import ParallelTaskExtras, TaskRuntimeContext
from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext


def _make_tool_context() -> ToolContext:
    return ToolContext(
        workspace_id="ws-001",
        workspace_path=Path("/tmp/ws"),
        session_id="sess-001",
    )


def test_tool_context_default_task_runtime_is_parallel_extras() -> None:
    ctx = _make_tool_context()
    assert isinstance(ctx.task_runtime, ParallelTaskExtras)
    assert ctx.task_runtime.parallel_task_store is None


def test_tool_context_task_runtime_ctx_reads_inner_context() -> None:
    ctx = _make_tool_context()
    ctx.task_runtime = ParallelTaskExtras(
        task_runtime=TaskRuntimeContext(parallel_task_id="pt-001"),
        parallel_task_store="fake-store",
    )

    assert ctx.task_runtime_ctx.parallel_task_id == "pt-001"
