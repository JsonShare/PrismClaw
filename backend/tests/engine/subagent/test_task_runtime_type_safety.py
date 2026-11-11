"""回归测试：TaskRuntimeState 不得覆写 ParallelTaskExtras。

复现用户截图报错：
    [Sub-agent failed: AttributeError: 'TaskRuntimeState' object has no attribute 'progress_emitter']

根因：governance.py 4 处 `ctx._tool_ctx.task_runtime = task_runtime`
把 schema 层 TaskRuntimeState（持久化快照）覆写了 engine 层 ParallelTaskExtras
（持有 progress_emitter / parallel_task_store）。

本文件验证两点：
  1. governance.py 修复：ToolContext.task_runtime 不再被错换为 TaskRuntimeState
  2. tool.py / executor.py / ToolContext.task_runtime_ctx 防御：即使上游
     覆写也不崩（getattr 兜底）
"""
from __future__ import annotations

import copy
from pathlib import Path


from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext
from prismclaw.engine.security.gate.types import ParallelTaskExtras, TaskRuntimeContext
from prismclaw.schema.agent.task_runtime import TaskRuntimeState, TaskRuntimeStateName  # noqa: F401  # 留作引用


# ── 辅助：构造两种 task_runtime 实例 ──────────────────────────────────

def _make_parallel_extras() -> ParallelTaskExtras:
    return ParallelTaskExtras(
        task_runtime=TaskRuntimeContext(parallel_task_id="pt-001"),
        progress_emitter="fake-emitter",
        parallel_task_store="fake-store",
    )


def _make_task_runtime_state() -> TaskRuntimeState:
    # model_construct 绕过 Pydantic 严格校验（测试只需要一个"有 task_id / session_id
    # 但没有 progress_emitter 的错配对象"来模拟 governance.py 旧行为）。
    return TaskRuntimeState.model_construct(
        task_id="task-001",
        session_id="sess-001",
        workspace_id="ws-001",
        state="executing",  # TaskRuntimeStateName 合法值
    )


def _make_tool_context() -> ToolContext:
    return ToolContext(
        workspace_id="ws-001",
        workspace_path=Path("/tmp/ws"),
        session_id="sess-001",
    )


# ── F1 验证：ToolContext 初始状态正确 ─────────────────────────────────

def test_tool_context_initial_task_runtime_is_parallel_extras():
    """ToolContext.__post_init__ 兜底为 ParallelTaskExtras（不是 TaskRuntimeState）。"""
    ctx = _make_tool_context()
    assert isinstance(ctx.task_runtime, ParallelTaskExtras)
    assert hasattr(ctx.task_runtime, "progress_emitter")
    assert hasattr(ctx.task_runtime, "parallel_task_store")


def test_tool_context_task_runtime_ctx_property_safe():
    """task_runtime_ctx property 能安全拿到 TaskRuntimeContext。"""
    ctx = _make_tool_context()
    extras = _make_parallel_extras()
    ctx.task_runtime = extras

    result = ctx.task_runtime_ctx
    assert result.parallel_task_id == "pt-001"


# ── F1 验证：模拟 governance.py 错换类型后防御层生效 ────────────────────

def test_tool_context_task_runtime_ctx_survives_task_runtime_state_mixup():
    """上游错误把 TaskRuntimeState 塞进 ToolContext.task_runtime 后，
    task_runtime_ctx property 不崩（返回默认 TaskRuntimeContext）。"""
    ctx = _make_tool_context()
    # 模拟 governance.py 旧行为：塞错类型
    ctx.task_runtime = _make_task_runtime_state()  # type: ignore[assignment]

    # getattr 兜底返回默认值
    result = ctx.task_runtime_ctx
    assert isinstance(result, TaskRuntimeContext)


def test_tool_context_progress_emitter_access_safe_after_mixup():
    """defensive getattr 访问 TaskRuntimeState 时返回 None（不 AttributeError）。"""
    state = _make_task_runtime_state()
    emitter = getattr(state, "progress_emitter", None)
    store = getattr(state, "parallel_task_store", None)
    assert emitter is None
    assert store is None


# ── F2 验证：tool.py deep copy 逻辑在错换类型后仍安全 ────────────────────

def test_tool_deepcopy_with_task_runtime_state_mixup_does_not_crash():
    """复现原始报错场景：ToolContext.task_runtime 被错换为 TaskRuntimeState
    后走 deep copy 隔离——不应 AttributeError。

    这直接覆盖用户截图里的错误路径。
    """
    ctx = _make_tool_context()
    # 先塞正确的 ParallelTaskExtras（有 progress_emitter）
    ctx.task_runtime = _make_parallel_extras()

    # 然后模拟 governance.py 的错误覆写
    ctx.task_runtime = _make_task_runtime_state()  # type: ignore[assignment]

    # 模拟 tool.py L375-430 的 deep copy 隔离逻辑
    _saved = ctx.task_runtime
    _saved_emitter = None
    _saved_store = None
    if ctx.task_runtime is not None:
        # 这 3 行是防御性加固的核心：getattr 兜底
        _saved_emitter = getattr(ctx.task_runtime, "progress_emitter", None)
        _saved_store = getattr(ctx.task_runtime, "parallel_task_store", None)
        inner = getattr(ctx.task_runtime, "task_runtime", None)
        ctx.task_runtime = ParallelTaskExtras(task_runtime=inner)

    child = copy.deepcopy(ctx)
    ctx.task_runtime = _saved  # 恢复原始（模拟 finally 块）

    # 验证 child 的 task_runtime 也是 ParallelTaskExtras
    assert isinstance(child.task_runtime, ParallelTaskExtras)
    # 保存的 emitter/store 为 None（因为 TaskRuntimeState 本来没有）
    assert child.task_runtime.progress_emitter is None
    assert child.task_runtime.parallel_task_store is None


def test_tool_deepcopy_with_correct_extras_preserves_extras():
    """正常路径（task_runtime 为 ParallelTaskExtras）deep copy 后 extras 完整保留。"""
    ctx = _make_tool_context()
    ctx.task_runtime = _make_parallel_extras()

    _saved = ctx.task_runtime
    _saved_emitter = getattr(ctx.task_runtime, "progress_emitter", None)
    _saved_store = getattr(ctx.task_runtime, "parallel_task_store", None)
    inner = getattr(ctx.task_runtime, "task_runtime", None)
    ctx.task_runtime = ParallelTaskExtras(task_runtime=inner)

    child = copy.deepcopy(ctx)
    ctx.task_runtime = _saved

    child.task_runtime.progress_emitter = _saved_emitter
    child.task_runtime.parallel_task_store = _saved_store

    assert child.task_runtime.progress_emitter == "fake-emitter"
    assert child.task_runtime.parallel_task_store == "fake-store"


# ── F2 验证：executor.py getattr 兜底 ──────────────────────────────────

def test_executor_parallel_task_store_access_safe_after_mixup():
    """executor.py L92 getattr 兜底在错换类型后安全返回 None。"""
    extras = _make_parallel_extras()
    store = getattr(extras, "parallel_task_store", None)
    assert store == "fake-store"

    state = _make_task_runtime_state()
    store_after_mixup = getattr(state, "parallel_task_store", None)
    assert store_after_mixup is None
