from __future__ import annotations

from prismclaw.engine.execution.loop._bootstrap_helpers import (
    _build_task_runtime_from_spec,
)
from prismclaw.engine.execution.loop.deps import AgentRunnerDeps
from prismclaw.engine.execution.loop.tree_writer import (
    _persist_review_requirements_snapshot,
    _persist_task_runtime_snapshot,
    _persist_task_spec_snapshot,
)
from prismclaw.schema.agent.task_runtime import ReviewRequirements, TaskRuntimeState
from prismclaw.schema.agent.task_types import TaskSpec
from prismclaw.schema.session.types import SessionTree


class EntryRuntimeBootstrapError(RuntimeError):
    def __init__(self, *, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(f"entry runtime bootstrap failed: {reason_code}")

    @property
    def error_code(self) -> str:
        return "entry_runtime_bootstrap_failed"

    @property
    def user_message(self) -> str:
        return "entry runtime bootstrap failed"


async def _bootstrap_entry_runtime(
    *,
    deps: AgentRunnerDeps,
    tree: SessionTree,
    task_spec: TaskSpec,
    review_requirements: ReviewRequirements,
    workspace_id: str | None,
    persist_runtime: bool = True,
) -> TaskRuntimeState:
    """Build the minimal in-memory entry runtime and persist the structural
    snapshots required before mode resolution.

    The entry runtime is never double-written as a ``pending`` intermediate.
    For a new task it is persisted exactly once later, after entry resolution
    succeeds (``ready``). For recovery runs it is persisted here so failure
    observability is preserved.
    """
    task_runtime = _build_task_runtime_from_spec(
        task_spec=task_spec,
        workspace_id=workspace_id,
    )
    try:
        await _persist_task_spec_snapshot(deps, tree, task_spec=task_spec)
        if persist_runtime:
            await _persist_task_runtime_snapshot(deps, tree, task_runtime=task_runtime)
        await _persist_review_requirements_snapshot(
            deps,
            tree,
            review_requirements=review_requirements,
        )
    except Exception as exc:
        raise EntryRuntimeBootstrapError(reason_code="snapshot_persist_failed") from exc
    return task_runtime
