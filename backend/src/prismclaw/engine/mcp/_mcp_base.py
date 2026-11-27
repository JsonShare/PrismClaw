"""Base class declaring shared attributes for MCPClientManager mixin composition.

All mixins (ConfigMixin, ConnectionMixin, LifecycleMixin, RegistryMixin, AdapterMixin)
reference these attributes set by MCPClientManager.__init__. Declaring them
here as type annotations allows static analysis to resolve them correctly.
"""

from __future__ import annotations

import asyncio
from collections import deque
from typing import TYPE_CHECKING, Any, Literal, TextIO

from prismclaw.engine.mcp._types import (
    ConnectionSummary,
    MCPRuntimeTelemetry,
    RestartResult,
    ServerStatus,
    _SupervisorCommand,
)

if TYPE_CHECKING:
    import mcp

    from prismclaw.engine.mcp.settings import McpServerConfig
    from prismclaw.engine.security.sandbox.policy import SandboxPolicy


class _MCPBackendBase:
    """Declares attributes shared across all MCP mixins.

    These are set by MCPClientManager.__init__ and read by mixin methods.
    """

    # Set in MCPClientManager.__init__
    _settings: Any  # McpSettings
    _servers: dict[str, ServerStatus]
    _adapters: dict[str, list[Any]]
    _sessions: dict[str, Any]
    _registry: Any  # _ToolRegistryProto | None
    _group: Any  # PrismClawClientSessionGroup | None
    _locks: dict[str, asyncio.Lock]
    _stderr_buffers: dict[str, deque[str]]
    _stderr_files: dict[str, TextIO]
    _stderr_pumps: dict[str, asyncio.Task[Any]]
    _sandbox_policy: SandboxPolicy | None
    _sandbox_advisories_emitted: bool
    _runtime_telemetry: dict[str, MCPRuntimeTelemetry]
    _supervisor_task: asyncio.Task[Any] | None
    _supervisor_task_id: str | None
    _loop: asyncio.AbstractEventLoop | None
    _shutdown_requested: asyncio.Event
    _supervisor_commands: asyncio.Queue[_SupervisorCommand]
    ready: asyncio.Event
    _task_manager: Any  # TaskManager
    runtime_observer: Any
    _impl_to_config: dict[str, str]
    _expert_name: str | None  # 归属专家包；None = 核心 server

    # Defined in ConnectionMixin — declared here for cross-mixin type resolution
    async def _connect_one(self, name: str, config: McpServerConfig) -> None:
        raise NotImplementedError

    async def _restart_server_impl(self, name: str) -> RestartResult:
        raise NotImplementedError

    # Defined in AdapterMixin — declared here for cross-mixin type resolution
    def _build_adapters_for_session(
        self, server_name: str, config: McpServerConfig, session: mcp.ClientSession
    ) -> list[Any]:
        raise NotImplementedError

    # Defined in RegistryMixin — declared here for cross-mixin type resolution
    def connection_summary(self) -> ConnectionSummary:
        raise NotImplementedError

    # Defined in ConfigMixin — declared here for cross-mixin type resolution
    def _status_with_runtime(self, status: ServerStatus) -> ServerStatus:
        raise NotImplementedError

    def note_tool_decision(
        self,
        server_name: str,
        *,
        tool_name: str,
        decision: Literal["approved", "denied"],
        reason: str | None = None,
    ) -> None:
        raise NotImplementedError

    def note_tool_result(
        self,
        server_name: str,
        *,
        tool_name: str,
        outcome: Literal["success", "error", "timeout", "server_dead"],
        detail: str | None = None,
    ) -> None:
        raise NotImplementedError

    def _enabled_servers(self) -> list[tuple[str, Any]]:
        raise NotImplementedError

    def _stop_stderr_pump(self, name: str) -> None:
        raise NotImplementedError

    def _get_lock(self, name: str) -> asyncio.Lock:
        raise NotImplementedError

    def _require_sandbox_policy(self) -> SandboxPolicy:
        raise NotImplementedError

    def _initialize_server_statuses(self) -> None:
        raise NotImplementedError
