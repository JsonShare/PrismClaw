"""MCPClientManager — owns the lifecycle of all configured MCP servers.

One instance per PrismClaw process. Constructed in :func:`prismclaw.app._lifespan`
during startup. Connects to servers in a non-blocking background supervisor
task (via :meth:`start_background`) so the FastAPI lifespan does NOT block
on per-server ``connect_timeout_seconds``. The agent factory hook calls
:meth:`attach_and_register` to bind the manager to a runner's
:class:`ToolRegistry`; servers connecting after that point register their
adapters live (per §5.5 of the spec).

The manager exposes:

* :meth:`start_background` — non-blocking; returns the supervisor task id
  (or ``None`` if the underlying ``TaskManager`` is already closed).
* :attr:`ready` — :class:`asyncio.Event` set when the supervisor's
  ``gather`` returns (success OR all-failed). ``try/finally`` in
  ``_run_supervisor`` guarantees this is set even on supervisor crash.
* :meth:`is_ready` / :meth:`connection_summary` — synchronous probes for
  ``/health`` and ``/mcp list``.
* :meth:`attach_and_register` — synchronous, idempotent. Binds a
  :class:`ToolRegistry` and bulk-registers any already-connected
  adapters.
* :meth:`restart_server` — per-server lock, atomic adapter swap on
  success, removes old adapters on failure (the spec's safer-by-default
  semantic).
* :meth:`get_logs` — stderr ring buffer with secret redaction.
* :meth:`list_servers` — returns one :class:`ServerStatus` per
  configured server.
* :meth:`shutdown` — cancels supervisor + drains MCP-tagged tasks +
  closes the underlying ``ClientSessionGroup``.

Architectural invariant: ``prismclaw.engine.*`` MUST NOT import this module.
Wiring is via ``deps.mcp_death_handler`` (a bound coroutine published into
``ToolContext``) and the ``external_tool_registrar`` callback on
the agent factory.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import TYPE_CHECKING, Any

import mcp
from mcp.client.session_group import ClientSessionGroup

from prismclaw.engine.integrations.mcp._composite import CompositeMCPManager
from prismclaw.engine.integrations.mcp._declarative import DeclarativeMCPManager
from prismclaw.engine.integrations.mcp._patch import _install_mcp_stdio_client_patch
from prismclaw.engine.integrations.mcp._types import (
    ConnectionSummary,
    RestartResult,
    ServerStatus,
    _SupervisorCommand,
)
from prismclaw.engine.integrations.mcp._utils import _is_unfilled_placeholder, _ToolRegistryProto
from prismclaw.engine.integrations.mcp.settings import McpSettings

from prismclaw.engine.integrations.mcp._mixin_config import ConfigMixin
from prismclaw.engine.integrations.mcp._mixin_connection import ConnectionMixin
from prismclaw.engine.integrations.mcp._mixin_lifecycle import LifecycleMixin
from prismclaw.engine.integrations.mcp._mixin_registry import RegistryMixin
from prismclaw.engine.integrations.mcp._mixin_adapter import AdapterMixin

if TYPE_CHECKING:
    from prismclaw.schema.services.task_protocol import TaskManagerProtocol

logger = logging.getLogger(__name__)


class PrismClawClientSessionGroup(ClientSessionGroup):
    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_val: BaseException | None,
        _exc_tb: Any,
    ) -> bool | None:
        # Upstream ClientSessionGroup registers each session stack in the main
        # exit stack and also keeps it in _session_exit_stacks. Closing both
        # paths in __aexit__ double-closes stdio async context managers.
        if self._owns_exit_stack:
            await self._exit_stack.aclose()
        else:
            for exit_stack in list(self._session_exit_stacks.values()):
                await exit_stack.aclose()
        self._session_exit_stacks.clear()
        self._sessions.clear()
        self._tool_to_session.clear()
        self._tools.clear()
        self._resources.clear()
        self._prompts.clear()
        return None


class MCPClientManager(ConfigMixin, ConnectionMixin, LifecycleMixin, RegistryMixin, AdapterMixin):
    def __init__(
        self,
        settings: McpSettings,
        task_manager: TaskManagerProtocol,
    ) -> None:
        self._settings = settings
        self._task_manager = task_manager
        self._adapters: dict[str, list[Any]] = {}
        self._servers: dict[str, ServerStatus] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._impl_to_config: dict[str, str] = {}
        self._registry: _ToolRegistryProto | None = None
        self._stderr_buffers: dict[str, deque[str]] = {}
        self._stderr_files: dict[str, Any] = {}
        self._stderr_pumps: dict[str, asyncio.Task] = {}
        self._runtime_telemetry: dict[str, Any] = {}
        self._supervisor_task_id: str | None = None
        self._supervisor_task: asyncio.Task | None = None
        self._shutdown_requested: asyncio.Event = asyncio.Event()
        self._supervisor_commands: asyncio.Queue[_SupervisorCommand] = asyncio.Queue()
        self._group: ClientSessionGroup = PrismClawClientSessionGroup()
        self._sessions: dict[str, mcp.ClientSession] = {}
        self._sandbox_policy: Any = None
        self._sandbox_advisories_emitted: bool = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self.ready: asyncio.Event = asyncio.Event()
        _install_mcp_stdio_client_patch()
        self._initialize_server_statuses()


__all__ = [
    "ConnectionSummary",
    "MCPClientManager",
    "RestartResult",
    "ServerStatus",
]
