"""Base class declaring shared attributes for MCPClientManager mixin composition.

All mixins (ConfigMixin, ConnectionMixin, LifecycleMixin, RegistryMixin, AdapterMixin)
reference these attributes set by MCPClientManager.__init__. Declaring them
here as type annotations allows static analysis to resolve them correctly.
"""
from __future__ import annotations

import asyncio
from collections import deque
from typing import Any

from prismclaw.engine.integrations.mcp._types import (
    ConnectionSummary,
    MCPRuntimeTelemetry,
    RestartResult,
    ServerStatus,
    _SupervisorCommand,
)


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
    _stderr_files: dict[str, Any]
    _stderr_pumps: dict[str, asyncio.Task]
    _sandbox_policy: Any | None
    _sandbox_advisories_emitted: bool
    _runtime_telemetry: dict[str, MCPRuntimeTelemetry]
    _supervisor_task: asyncio.Task | None
    _supervisor_task_id: str | None
    _loop: asyncio.AbstractEventLoop | None
    _shutdown_requested: asyncio.Event
    _supervisor_commands: asyncio.Queue[_SupervisorCommand]
    ready: asyncio.Event
    _task_manager: Any  # TaskManager
    runtime_observer: Any
    _impl_to_config: dict[str, str]

    # Defined in ConnectionMixin — declared here for cross-mixin type resolution
    async def _connect_one(self, name: str, config: Any) -> None: ...
    async def _restart_server_impl(self, name: str) -> RestartResult: ...

    # Defined in AdapterMixin — declared here for cross-mixin type resolution
    def _build_adapters_for_session(self, server_name: str, config: Any, session: Any) -> list[Any]: ...

    # Defined in RegistryMixin — declared here for cross-mixin type resolution
    def connection_summary(self) -> ConnectionSummary: ...

    # Defined in ConfigMixin — declared here for cross-mixin type resolution
    def _status_with_runtime(self, status: ServerStatus) -> ServerStatus: ...
    def _enabled_servers(self) -> list[tuple[str, Any]]: ...
    def _get_lock(self, name: str) -> asyncio.Lock: ...
    def _require_sandbox_policy(self) -> Any: ...
    def _initialize_server_statuses(self) -> None: ...
