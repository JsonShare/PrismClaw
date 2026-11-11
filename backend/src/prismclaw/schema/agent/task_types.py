from __future__ import annotations

"""Task specification types shared across layers.

Moved from kernel/agent.py to contracts so that contracts-level Protocols
can reference these types without TYPE_CHECKING coupling to kernel.
"""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from prismclaw.schema.agent.task_runtime import PreflightProbeResult

TaskRiskLevel = Literal["normal", "high"]


class TaskSpec(BaseModel):
    task_id: str
    session_id: str
    turn_index: int = 1
    goal: str
    risk_level: TaskRiskLevel = "normal"
    constraints: list[str] = Field(default_factory=list)
    source_message: str = ""
    attachments_present: bool = False
    notes: list[str] = Field(default_factory=list)
    preflight_probe: PreflightProbeResult | None = None


TaskCategory = Literal[
    "heartbeat", "consumer", "archive", "nudge", "evolution", "curator", "mcp", "dream", "generic"
]
TaskState = Literal["running", "done", "cancelled", "failed"]


@dataclass
class TaskInfo:
    """Snapshot of a managed task's state."""

    task_id: str
    name: str
    category: TaskCategory
    state: TaskState
    created_at: float
    duration_s: float
    exception: str | None
    owner: str | None = None
    result_payload: object | None = None


__all__ = ["TaskSpec", "TaskRiskLevel", "TaskCategory", "TaskState", "TaskInfo"]
