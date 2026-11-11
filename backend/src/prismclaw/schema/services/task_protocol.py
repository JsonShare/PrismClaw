"""TaskManager protocol — engine layer depends on this, not the concrete impl."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from prismclaw.schema.agent.task_types import TaskCategory, TaskInfo, TaskState


@runtime_checkable
class TaskManagerProtocol(Protocol):
    """Minimal protocol for the task management interface used by engine layer."""

    _tasks: dict[str, Any]

    def spawn(
        self,
        name: str,
        coro: Any,
        *,
        category: TaskCategory = "generic",
        on_error: Any | None = None,
        owner: str | None = None,
    ) -> str:
        """Spawn a background task; returns task_id."""
        ...

    async def cancel(self, task_id: str, *, timeout: float = 5.0) -> bool:
        """Cancel a running task."""
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

    async def shutdown(self, grace_s: float | None = None) -> Any:
        """Shut down the task manager."""
        ...
