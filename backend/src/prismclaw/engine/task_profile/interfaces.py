from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from prismclaw.schema.session import SessionTree

from prismclaw.schema.llm.request_mode import RequestMode
from prismclaw.engine.task_profile.service import (
    TaskCompactionPolicy,
    TaskResponsePolicy,
)
from prismclaw.schema.content.context import TaskProfile


@runtime_checkable
class TaskRequestPolicyProvider(Protocol):
    def resolve_request_mode(
        self,
        *,
        tree: "SessionTree",
        run_seconds: int = 300,
        channel: str = "cli",
    ) -> RequestMode | None: ...

    def runtime_strategy_notes(
        self,
        task_profile: TaskProfile,
        *,
        request_mode: RequestMode | None,
    ) -> list[str]: ...

    def retrieval_limits(
        self,
        task_profile: TaskProfile,
        *,
        l2_quota: int,
        l3_quota: int,
        archive_limit: int,
        request_mode: RequestMode | None,
    ) -> tuple[int, int, int]: ...


@runtime_checkable
class TaskRuntimePolicyProvider(Protocol):
    def response_policy(
        self,
        task_profile: TaskProfile,
        *,
        config: Any,
    ) -> TaskResponsePolicy: ...

    def compaction_policy(
        self,
        task_profile: TaskProfile,
        *,
        history_budget: int,
        history_threshold: float,
        keep_recent_tokens: int,
    ) -> TaskCompactionPolicy: ...
