from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from prismclaw.engine.task_profile.service import (
    TaskCompactionPolicy,
    TaskResponsePolicy,
)
from prismclaw.schema.content.context import TaskProfile

if TYPE_CHECKING:
    from prismclaw.schema.config.config import AgentRunConfig


@runtime_checkable
class TaskRequestPolicyProvider(Protocol):
    def runtime_strategy_notes(
        self,
        task_profile: TaskProfile,
    ) -> list[str]: ...


@runtime_checkable
class TaskRuntimePolicyProvider(Protocol):
    def response_policy(
        self,
        task_profile: TaskProfile,
        *,
        config: AgentRunConfig,
    ) -> TaskResponsePolicy: ...

    def compaction_policy(
        self,
        task_profile: TaskProfile,
        *,
        history_budget: int,
        history_threshold: float,
        keep_recent_tokens: int,
    ) -> TaskCompactionPolicy: ...
