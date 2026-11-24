"""确认门 mutation 语义：approve 统一收敛为 task.confirmation_resolved。"""

from __future__ import annotations

from prismclaw.engine.execution.state.task_persistence import (
    _apply_task_mutation_to_runtime,
    _new_task_mutation,
)
from prismclaw.schema.agent.task_runtime import TaskRuntimeState


def _runtime(state: str = "awaiting_risk_confirmation") -> TaskRuntimeState:
    return TaskRuntimeState(
        task_id="t1",
        session_id="s1",
        state=state,  # type: ignore[arg-type]
        pending_confirmation_reason="高风险",
    )


def test_confirmation_resolved_resumes_execution() -> None:
    runtime = _runtime()
    mutation = _new_task_mutation(
        runtime,
        actor_kind="system",
        mutation_type="task.confirmation_resolved",
    )

    updated = _apply_task_mutation_to_runtime(task_runtime=runtime, mutation=mutation)

    assert updated.state == "executing"
    assert updated.pending_confirmation_reason is None
    assert updated.state_version == runtime.state_version + 1


def test_block_records_reason() -> None:
    runtime = _runtime("awaiting_confirmation")
    mutation = _new_task_mutation(
        runtime,
        actor_kind="system",
        mutation_type="task.block",
        patch={"reason": "工作区已变更"},
    )

    updated = _apply_task_mutation_to_runtime(task_runtime=runtime, mutation=mutation)

    assert updated.state == "blocked"
    assert updated.pending_confirmation_reason == "工作区已变更"
