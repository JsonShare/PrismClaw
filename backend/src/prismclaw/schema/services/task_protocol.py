"""TaskManager protocol — engine layer depends on this, not the concrete impl."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any, Protocol, runtime_checkable

from prismclaw.schema.agent.task_types import TaskCategory, TaskInfo, TaskState

TaskCoro = Coroutine[Any, Any, Any]


@runtime_checkable
class TaskManagerProtocol(Protocol):
    """Minimal protocol for the task management interface used by engine layer."""

    _tasks: dict[str, Any]

    def spawn(
        self,
        name: str,
        coro: TaskCoro,
        *,
        category: TaskCategory = "generic",
        on_error: Callable[[BaseException], None] | None = None,
        owner: str | None = None,
    ) -> str:
        """Spawn a background task; returns task_id."""
        ...

    async def cancel(self, task_id: str, *, timeout: float = 5.0) -> bool:  # noqa: ASYNC109
        """Cancel a running task.

        ``timeout`` 是等待任务退出的上限；实现需据此判断是否打
        「取消超时」告警，无法用调用方的 ``asyncio.timeout`` 替代。
        """
        ...

    def get_state(self, task_id: str) -> TaskState | None:
        """Return task state or None if unknown."""
        ...

    def list_tasks(
        self,
        *,
        category: TaskCategory | None = None,
        include_done: bool = False,
        owner: str | None = None,
    ) -> list[TaskInfo]:
        """List active tasks."""
        ...

    async def shutdown(self, grace_s: float | None = None) -> object:
        """Shut down the task manager."""
        ...
