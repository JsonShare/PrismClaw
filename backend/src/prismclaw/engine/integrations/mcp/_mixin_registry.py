"""Auto-extracted Mixin from client_manager.py — DO NOT edit method signatures."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import mcp

from prismclaw.engine.integrations.mcp._types import (
    ConnectionSummary,
    ServerStatus,
)
from prismclaw.engine.integrations.mcp._utils import (
    _ToolRegistryProto,
    _redact_config_value,
    _render_prompt_result_text,
)
from prismclaw.engine.integrations.mcp.settings import McpServerConfig
from prismclaw.schema.shared.exceptions import MCPServerDeadError

from prismclaw.engine.integrations.mcp._mcp_base import _MCPBackendBase

logger = logging.getLogger(__name__)


class RegistryMixin(_MCPBackendBase):
    def attach_and_register(self, registry: _ToolRegistryProto) -> None:
        if self._registry is registry:
            return
        if self._registry is not None:
            logger.warning(
                "mcp.attach_and_register.rebind from %s to %s",
                id(self._registry),
                id(registry),
            )
            for adapters in self._adapters.values():
                for adapter in adapters:
                    self._registry.unregister(adapter.name)
        self._registry = registry
        for adapters in self._adapters.values():
            for adapter in adapters:
                registry.register(adapter)

    def get_logs(self, name: str) -> str:
        capture = self._stderr_files.get(name)
        if capture is not None:
            try:
                capture.flush()
                capture.seek(0)
                joined = capture.read()
            except Exception:
                logger.debug("mcp.stderr_capture read error", exc_info=True)
                joined = ""
        else:
            buffer = self._stderr_buffers.get(name)
            if buffer is None:
                return ""
            joined = "\n".join(buffer)
        config = self._settings.servers.get(name)
        if config is None:
            return joined
        return self._redact_logs(joined, config)

    def _redact_logs(self, joined: str, config: McpServerConfig) -> str:
        for raw_value in config.env.values():
            joined = _redact_config_value(joined, raw_value)
        for raw_value in config.args:
            joined = _redact_config_value(joined, raw_value)
        for raw_value in config.headers.values():
            joined = _redact_config_value(joined, raw_value)
        return joined

    def list_servers(self) -> list[ServerStatus]:
        return [self._status_with_runtime(self._servers[name]) for name in self._settings.servers]

    def get_status(self, name: str) -> ServerStatus | None:
        status = self._servers.get(name)
        if status is None:
            return None
        return self._status_with_runtime(status)

    def get_session(self, name: str) -> mcp.ClientSession | None:
        status = self._servers.get(name)
        if status is None or status.status != "connected":
            return None
        return self._sessions.get(name)

    def list_mcp_prompts(self) -> list[tuple[str, Any]]:
        """Return ``(server_name, Prompt)`` pairs for every connected server.

        Synchronous: relies on prompts already cached by ``ClientSessionGroup``
        during connection (``_aggregate_components``). Server attribution is
        reconstructed by matching each cached session object to its
        ``ComponentNames`` bookkeeping in the group.
        """
        result: list[tuple[str, Any]] = []
        group: Any = self._group
        session_to_names: Any = getattr(group, "_sessions", None)
        group_prompts: Any = getattr(group, "_prompts", None)
        if not isinstance(session_to_names, dict) or not isinstance(group_prompts, dict):
            return result
        for server_name, session in self._sessions.items():
            status = self._servers.get(server_name)
            if status is None or getattr(status, "status", None) != "connected":
                continue
            component_names = session_to_names.get(session)
            if component_names is None:
                continue
            prompt_names = getattr(component_names, "prompts", None)
            if not isinstance(prompt_names, (set, list)):
                continue
            for name in prompt_names:
                prompt = group_prompts.get(name)
                if prompt is not None:
                    result.append((server_name, prompt))
        return result

    async def get_prompt_text(
        self,
        server_name: str,
        prompt_name: str,
        arguments: dict[str, str] | None = None,
    ) -> str:
        """Execute ``prompts/get`` on a connected server and render the result.

        Raises:
            MCPServerDeadError: server not connected (or died mid-call).
            MCPError: protocol-level failure from the server.
        """
        session = self.get_session(server_name)
        if session is None:
            raise MCPServerDeadError(
                f"MCP server {server_name!r} is not connected; "
                f"cannot fetch prompt {prompt_name!r}"
            )
        result = await session.get_prompt(prompt_name, arguments=arguments or None)
        return _render_prompt_result_text(result)

    def get_prompt_text_threadsafe(
        self,
        server_name: str,
        prompt_name: str,
        arguments: dict[str, str] | None = None,
        *,
        timeout_s: float = 15.0,
    ) -> str:
        """Blocking variant for worker threads (e.g. skill runtime executor).

        Schedules :meth:`get_prompt_text` onto the manager's event loop via
        ``run_coroutine_threadsafe``. MUST NOT be called from the event-loop
        thread itself (would deadlock); callers are executor threads only.
        """
        loop = self._loop
        if loop is None or loop.is_closed():
            raise MCPServerDeadError(
                f"MCP manager loop unavailable; cannot fetch prompt "
                f"{prompt_name!r} from server {server_name!r}"
            )
        future = asyncio.run_coroutine_threadsafe(
            self.get_prompt_text(server_name, prompt_name, arguments), loop
        )
        return future.result(timeout=timeout_s)

    def connection_summary(self) -> ConnectionSummary:
        summary = ConnectionSummary()
        for status in self._servers.values():
            if status.status == "connected":
                summary.n_connected += 1
                summary.total_tools += status.tool_count
            elif status.status == "failed":
                summary.n_failed += 1
            elif status.status == "pending":
                summary.n_pending += 1
            elif status.status == "disabled":
                summary.n_disabled += 1
        return summary
