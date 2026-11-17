"""P1a：后台子代理派的契约测试。

覆盖设计文档第 9 节的四项关键测试：
1. 不阻塞：``dispatch_mode=background`` 立即返回 task_id，回执不含结果正文
2. 记录唯一性：一次派发只写一条 scheduled / running / 终态
3. 取消：``cancel_task`` 真实取消后台 task，并落 cancelled 终态
4. 停机：TaskManager 排水时后台任务落终态
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from prismclaw.engine.security.gate.types import ParallelTaskExtras, TaskRuntimeContext
from prismclaw.engine.subagent.core import executor as executor_mod
from prismclaw.engine.subagent.core._result_types import AgentRunResult
from prismclaw.engine.subagent.core.executor import SubAgentExecutor
from prismclaw.engine.subagent.runtime import background as background_mod
from prismclaw.engine.subagent.runtime.parallel import (
    PARALLEL_TASK_TERMINAL_STATUSES,
    ParallelTaskRuntimeRecord,
)
from prismclaw.engine.subagent.tool import SubAgentTool
from prismclaw.engine.toolkit.builtin.task_tools import CancelTaskTool
from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext
from prismclaw.infra.task_manager import TaskManager
from prismclaw.schema.config.config import SubAgentConfig
from prismclaw.schema.content.content_types import TextBlock, ToolResult

# ── 测试替身 ──────────────────────────────────────────────────────────


class _MemoryTaskStore:
    """与 SessionParallelTaskStore 语义一致的进程内实现。"""

    def __init__(self) -> None:
        self.records: list[ParallelTaskRuntimeRecord] = []
        self._latest: dict[str, ParallelTaskRuntimeRecord] = {}

    async def append(self, record: ParallelTaskRuntimeRecord) -> None:
        self.records.append(record)
        self._latest[record.task_id] = record

    async def get(
        self, *, session_id: str, task_id: str
    ) -> ParallelTaskRuntimeRecord | None:
        return self._latest.get(task_id)

    async def list_for_session(
        self,
        *,
        session_id: str,
        include_terminal: bool = True,
        kinds=None,
    ) -> list[ParallelTaskRuntimeRecord]:
        values = list(self._latest.values())
        if not include_terminal:
            values = [
                r for r in values if r.status not in PARALLEL_TASK_TERMINAL_STATUSES
            ]
        if kinds is not None:
            values = [r for r in values if r.kind in kinds]
        return values


def _manifest(name: str = "researcher") -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        source="user",
        execution_mode="sync",
        context_mode="isolated",
        model="test-model",
        max_turns=1,
        timeout_seconds=1.0,
        declared_invoke_sequence=[],
    )


def _context(
    store: _MemoryTaskStore,
    manager: TaskManager,
    *,
    sink=None,
) -> ToolContext:
    return ToolContext(
        workspace_id="ws",
        workspace_path=Path("/tmp/ws"),
        session_id="sess-1",
        task_runtime=ParallelTaskExtras(
            task_runtime=TaskRuntimeContext(),
            parallel_task_store=store,
        ),
        task_manager=manager,
        subagent_terminal_sink=sink,
    )


def _success_result(text: str = "done") -> AgentRunResult:
    return AgentRunResult(
        tool_result=ToolResult(
            tool_call_id="call-1",
            content=[TextBlock(text=text)],
        ),
        outcome="success",
        turns=1,
        tokens_input=1,
        tokens_output=1,
        duration_seconds=0.01,
        model_used="test-model",
    )


def _patch_instance(monkeypatch: pytest.MonkeyPatch, behavior) -> None:
    class _FakeInstance:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        async def run(self, task, *, call_id="", context_snapshot=None):
            return await behavior()

    monkeypatch.setattr(executor_mod, "AgentInstance", _FakeInstance)


async def _wait_until(predicate, *, budget_s: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + budget_s
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met before timeout")


def _executor(settings: SubAgentConfig | None = None) -> SubAgentExecutor:
    return SubAgentExecutor(
        tool_registry=SimpleNamespace(),
        settings=settings or SubAgentConfig(),
        llm_client=SimpleNamespace(default_model="test-model"),
    )


# ── 1. 不阻塞 ────────────────────────────────────────────────────────


async def test_launch_background_returns_before_run_finishes(monkeypatch) -> None:
    async def behavior():
        await asyncio.sleep(0.2)
        return _success_result()

    _patch_instance(monkeypatch, behavior)
    store = _MemoryTaskStore()
    manager = TaskManager()
    executor = _executor()

    loop = asyncio.get_running_loop()
    started = loop.time()
    task_id, error = await executor.launch_background(
        _manifest(), "do it", _context(store, manager)
    )
    elapsed = loop.time() - started

    assert error is None
    assert task_id is not None and task_id.startswith("pt:")
    # 派发耗时不包含子代理执行时长（0.2s）
    assert elapsed < 0.1

    await _wait_until(
        lambda: store._latest.get(task_id) is not None
        and store._latest[task_id].status == "running"
    )
    await manager.shutdown()
    assert store._latest[task_id].status == "cancelled"


async def test_background_dispatch_result_contains_only_task_ids(monkeypatch) -> None:
    async def behavior():  # pragma: no cover - 不会被执行
        return _success_result("RESULT-BODY")

    _patch_instance(monkeypatch, behavior)
    store = _MemoryTaskStore()
    manager = TaskManager()
    captured: dict[str, ToolContext] = {}

    class _FakeExecutor:
        async def launch_background(
            self, manifest, task, ctx, *, call_id="", context_snapshot=None
        ):
            captured["ctx"] = ctx
            return "pt:abc123", None

        async def run(self, *args, **kwargs):  # pragma: no cover
            raise AssertionError("background dispatch must not await run()")

    tool = SubAgentTool(
        executor=_FakeExecutor(),
        registry=SimpleNamespace(),
        settings=SubAgentConfig(),
        capability_catalog=_Catalog(),
    )
    ctx = _context(store, manager, sink=lambda record: None)

    result = await tool.execute(
        {
            "_call_id": "call-1",
            "dispatch_mode": "background",
            "agents": [{"agent_name": "researcher", "task": "do it"}],
        },
        ctx,
    )

    text = result.content[0].text
    assert result.is_error is False
    assert "pt:abc123" in text
    assert "RESULT-BODY" not in text
    # 后台子上下文必须是原运行时的引用（deepcopy 后按引用回填）
    child = captured["ctx"]
    assert child.abort is ctx.abort
    assert child.task_manager is manager
    assert child.subagent_terminal_sink is ctx.subagent_terminal_sink


async def test_tool_does_not_expose_background_on_multi_replica() -> None:
    tool = SubAgentTool(
        executor=SimpleNamespace(),
        registry=SimpleNamespace(),
        settings=SubAgentConfig(),
        capability_catalog=_Catalog(),
        allow_background=False,
    )
    assert "dispatch_mode" not in tool.parameters["properties"]

    store = _MemoryTaskStore()
    manager = TaskManager()
    result = await tool.execute(
        {
            "_call_id": "call-1",
            "dispatch_mode": "background",
            "agents": [{"agent_name": "researcher", "task": "do it"}],
        },
        _context(store, manager),
    )
    assert result.is_error is True
    assert "不可用" in result.content[0].text


# ── 2. 记录唯一性 ────────────────────────────────────────────────────


async def test_background_writes_single_record_per_status(monkeypatch) -> None:
    _patch_instance(monkeypatch, lambda: _as_coro(_success_result()))
    store = _MemoryTaskStore()
    manager = TaskManager()
    executor = _executor()

    task_id, error = await executor.launch_background(
        _manifest(), "do it", _context(store, manager)
    )
    assert error is None and task_id is not None
    await _wait_until(
        lambda: store._latest.get(task_id) is not None
        and store._latest[task_id].status in PARALLEL_TASK_TERMINAL_STATUSES
    )

    statuses = [r.status for r in store.records if r.task_id == task_id]
    assert statuses == ["scheduled", "running", "completed"]
    assert store._latest[task_id].result_summary == "done"


async def test_sync_run_still_writes_same_record_count(monkeypatch) -> None:
    _patch_instance(monkeypatch, lambda: _as_coro(_success_result()))
    store = _MemoryTaskStore()
    manager = TaskManager()
    executor = _executor()

    result = await executor.run(
        _manifest(), "do it", _context(store, manager), call_id="call-1"
    )
    assert result.outcome == "success"
    statuses = [r.status for r in store.records]
    assert statuses == ["scheduled", "running", "completed"]


# ── 3. 取消 ──────────────────────────────────────────────────────────


async def test_cancel_task_really_cancels_background_run(monkeypatch) -> None:
    cancelled = asyncio.Event()

    async def behavior():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    _patch_instance(monkeypatch, behavior)
    store = _MemoryTaskStore()
    manager = TaskManager()
    executor = _executor()
    ctx = _context(store, manager)

    task_id, error = await executor.launch_background(_manifest(), "do it", ctx)
    assert error is None and task_id is not None
    await _wait_until(
        lambda: store._latest.get(task_id) is not None
        and store._latest[task_id].status == "running"
    )

    result = await CancelTaskTool().execute({"task_id": task_id}, ctx)

    assert result.is_error is False
    assert "已取消" in result.content[0].text
    assert cancelled.is_set()
    assert store._latest[task_id].status == "cancelled"
    assert task_id not in background_mod._background_handles


async def test_cancel_task_rejects_sync_record_and_unknown_handle(monkeypatch) -> None:
    store = _MemoryTaskStore()
    manager = TaskManager()
    await store.append(
        ParallelTaskRuntimeRecord(
            task_id="pt:sync",
            owner_session_id="sess-1",
            kind="subagent",
            status="running",
        )
    )
    ctx = _context(store, manager)

    result = await CancelTaskTool().execute({"task_id": "pt:sync"}, ctx)
    assert result.is_error is True
    assert "不是本进程可取消的后台任务" in result.content[0].text


# ── 4. 停机 ──────────────────────────────────────────────────────────


async def test_shutdown_writes_terminal_state_for_running_background_task(
    monkeypatch,
) -> None:
    async def behavior():
        await asyncio.Event().wait()

    _patch_instance(monkeypatch, behavior)
    store = _MemoryTaskStore()
    manager = TaskManager()
    executor = _executor()

    task_id, error = await executor.launch_background(
        _manifest(), "do it", _context(store, manager)
    )
    assert error is None and task_id is not None
    await _wait_until(
        lambda: store._latest.get(task_id) is not None
        and store._latest[task_id].status == "running"
    )

    report = await manager.shutdown()
    assert report.cancelled == 1
    assert store._latest[task_id].status == "cancelled"
    assert task_id not in background_mod._background_handles


# ── 5. 闸门与异常终态 ────────────────────────────────────────────────


async def test_parallel_gate_rejects_launch_over_limit(monkeypatch) -> None:
    async def behavior():
        await asyncio.Event().wait()

    _patch_instance(monkeypatch, behavior)
    store = _MemoryTaskStore()
    manager = TaskManager()
    executor = _executor(SubAgentConfig(max_parallel_per_turn=1))
    ctx = _context(store, manager)

    first_id, first_error = await executor.launch_background(_manifest(), "a", ctx)
    assert first_error is None and first_id is not None
    await _wait_until(
        lambda: store._latest.get(first_id) is not None
        and store._latest[first_id].status == "running"
    )

    second_id, second_error = await executor.launch_background(_manifest(), "b", ctx)
    assert second_id is None
    assert second_error is not None and "上限" in second_error

    await manager.shutdown()


async def test_background_run_crash_writes_failed_terminal(monkeypatch) -> None:
    async def behavior():
        raise RuntimeError("boom")

    _patch_instance(monkeypatch, behavior)
    store = _MemoryTaskStore()
    manager = TaskManager()
    executor = _executor()

    task_id, error = await executor.launch_background(
        _manifest(), "do it", _context(store, manager)
    )
    assert error is None and task_id is not None
    await _wait_until(
        lambda: store._latest.get(task_id) is not None
        and store._latest[task_id].status in PARALLEL_TASK_TERMINAL_STATUSES
    )
    assert store._latest[task_id].status == "failed"
    assert "boom" in (store._latest[task_id].error_summary or "")


async def test_parent_abort_really_cancels_background_run(monkeypatch) -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def behavior():
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    _patch_instance(monkeypatch, behavior)
    store = _MemoryTaskStore()
    manager = TaskManager()
    executor = _executor()
    ctx = _context(store, manager)

    task_id, error = await executor.launch_background(_manifest(), "do it", ctx)
    assert error is None and task_id is not None
    await asyncio.wait_for(started.wait(), timeout=1.0)
    await _wait_until(
        lambda: store._latest.get(task_id) is not None
        and store._latest[task_id].status == "running"
    )

    ctx.abort.set()

    await _wait_until(
        lambda: store._latest[task_id].status in PARALLEL_TASK_TERMINAL_STATUSES
    )
    assert cancelled.is_set()
    assert store._latest[task_id].status == "cancelled"
    assert task_id not in background_mod._background_handles
    await manager.shutdown()


async def test_task_tools_are_not_skill_gated() -> None:
    from prismclaw.engine.toolkit.builtin.task_tools import (
        CheckSubagentTaskTool,
        ListSubagentTasksTool,
    )

    # 派发回执直接引用这些工具；skill_gated 会让正常对话看不到它们。
    assert CheckSubagentTaskTool.skill_gated is False
    assert ListSubagentTasksTool.skill_gated is False
    assert CancelTaskTool.skill_gated is False
    assert CancelTaskTool.read_only_allowed is True
    assert CheckSubagentTaskTool.visibility_scope == "main_only"
    assert ListSubagentTasksTool.visibility_scope == "main_only"
    assert CancelTaskTool.visibility_scope == "main_only"


# ── 6. 终态投递 ──────────────────────────────────────────────────────


async def test_terminal_sink_receives_record_after_background_completion(
    monkeypatch,
) -> None:
    _patch_instance(monkeypatch, lambda: _as_coro(_success_result("the answer")))
    store = _MemoryTaskStore()
    manager = TaskManager()
    delivered: list[ParallelTaskRuntimeRecord] = []

    async def sink(record: ParallelTaskRuntimeRecord) -> None:
        delivered.append(record)

    executor = _executor()
    task_id, error = await executor.launch_background(
        _manifest(), "do it", _context(store, manager, sink=sink)
    )
    assert error is None and task_id is not None
    await _wait_until(lambda: bool(delivered))
    assert delivered[0].task_id == task_id
    assert delivered[0].status == "completed"
    assert delivered[0].result_summary == "the answer"


async def test_sync_run_does_not_deliver_terminal_notification(monkeypatch) -> None:
    _patch_instance(monkeypatch, lambda: _as_coro(_success_result()))
    store = _MemoryTaskStore()
    manager = TaskManager()
    delivered: list[ParallelTaskRuntimeRecord] = []

    async def sink(record: ParallelTaskRuntimeRecord) -> None:
        delivered.append(record)

    executor = _executor()
    await executor.run(
        _manifest(), "do it", _context(store, manager, sink=sink), call_id="call-1"
    )
    assert delivered == []


async def _as_coro(value):
    return value


class _Catalog:
    def count_agent_entries(self) -> int:
        return 1

    def get_agent_entry(self, name: str):
        if name != "researcher":
            return None
        return SimpleNamespace(manifest=_manifest())

    def list_agent_entries(self) -> list:
        return [SimpleNamespace(manifest=_manifest())]
