"""MCP (Model Context Protocol) client integration for PrismClaw.

Connects PrismClaw's agent to external MCP servers (filesystem, github, slack,
etc.) so their tools become indistinguishable from builtin tools at the runner
level. Reuses Sprint 1's permission tier system (:mod:`prismclaw.engine.hooks`)
without modification.

Public API:

* :class:`MCPClientManager` — owns the lifecycle of all configured servers.
  Constructed once per PrismClaw process (in the FastAPI lifespan); started via
  :meth:`MCPClientManager.start_background` (non-blocking); attached to the
  runner's :class:`ToolRegistry` via :meth:`MCPClientManager.attach_and_register`
  (called from the agent factory hook).
* :class:`MCPToolAdapter` — implements the duck-typed
  :class:`prismclaw.engine.toolkit.registry.Tool` Protocol; one per discovered
  remote tool. Carries dual names (``name`` canonical, ``_sdk_key`` for
  ``ClientSessionGroup.call_tool``).
* :class:`MCPServerDeadError` — raised by adapter ``execute()`` to signal a
  dead-server condition; caught inside ``_dispatch_single`` and converted
  into an error :class:`ToolResult` with a non-blocking
  ``_handle_server_death`` schedule.
* :class:`McpSettings`, :class:`McpServerConfig`, :class:`ServerStatus`,
  :class:`RestartResult`, :class:`ConnectionSummary` — Pydantic / dataclass
  types used in configuration, status reporting, and the ``/mcp`` slash
  command.

Architectural invariant: ``prismclaw.engine.*`` MUST NOT import from
``prismclaw.integrations.mcp.*`` *except* for :class:`MCPServerDeadError`
(via :mod:`prismclaw.integrations.mcp.errors`). Wiring is via the
``external_tool_registrar`` callback parameter on the agent factory and
duck-typing in the runner's per-call tier evaluation.
"""

from prismclaw.engine.integrations.mcp.adapter import MCPToolAdapter
from prismclaw.schema.shared.exceptions import MCPServerDeadError
from prismclaw.engine.integrations.mcp.settings import McpServerConfig, McpSettings

__all__ = [
    "MCPServerDeadError",
    "MCPToolAdapter",
    "McpServerConfig",
    "McpSettings",
]
