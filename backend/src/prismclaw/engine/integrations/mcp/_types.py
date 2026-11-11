from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    pass

_STDERR_BUFFER_LINES = 200
_TOOL_COUNT_WARN_THRESHOLD = 100

ServerStatusLiteral = Literal["connected", "failed", "disabled", "pending"]


@dataclass
class MCPRuntimeTelemetry:
    forced_chat_only: bool = False
    total_tool_calls: int = 0
    total_denied_calls: int = 0
    total_failed_calls: int = 0
    total_timed_out_calls: int = 0
    last_tool_name: str | None = None
    last_tool_call_at: datetime | None = None
    last_tool_decision_at: datetime | None = None
    last_tool_decision: Literal["approved", "denied"] | None = None
    last_tool_decision_reason: str | None = None
    last_tool_success_at: datetime | None = None
    last_tool_failure_at: datetime | None = None
    last_tool_error: str | None = None


@dataclass
class ServerStatus:
    name: str
    status: ServerStatusLiteral
    tool_count: int = 0
    last_connect_at: datetime | None = None
    reason: str | None = None
    runtime: MCPRuntimeTelemetry | None = None


@dataclass
class RestartResult:
    ok: bool
    tool_count: int = 0
    reason: str | None = None


@dataclass
class ConnectionSummary:
    n_connected: int = 0
    n_failed: int = 0
    n_pending: int = 0
    n_disabled: int = 0
    total_tools: int = 0


type _SupervisorCommand = tuple[str, str, asyncio.Future[RestartResult] | None]
