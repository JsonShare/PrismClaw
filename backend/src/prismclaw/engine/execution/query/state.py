from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from prismclaw.engine.context.projection_models import ProjectionMessageSet


@dataclass(frozen=True, slots=True)
class QueryViewDiagnostics:
    source_digest: str
    messages_digest: str
    source_message_count: int
    query_message_count: int


@dataclass(frozen=True, slots=True)
class QueryView:
    source_messages: ProjectionMessageSet
    messages_for_query: ProjectionMessageSet
    compression_state: Any | None
    attachment_state: Any | None
    revision_id: str
    diagnostics: QueryViewDiagnostics


@dataclass(frozen=True, slots=True)
class QueryRuntimeContext:
    query_source: str = "runner"
    capability_snapshot: Any | None = None
    tool_context: Any | None = None
    app_context: Any | None = None
    budget_policy: Any | None = None
    permission_context: Any | None = None
    model_context: Any | None = None
    session_hooks: Any | None = None


@dataclass(frozen=True, slots=True)
class QueryLoopTracking:
    tool_loop_state: Any | None = None


@dataclass(frozen=True, slots=True)
class QueryLoopState:
    query_view: QueryView
    runtime_context: QueryRuntimeContext
    tracking: QueryLoopTracking
    loop_revision_id: str
