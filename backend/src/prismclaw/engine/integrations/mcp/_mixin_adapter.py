"""Auto-extracted Mixin from client_manager.py — DO NOT edit method signatures."""
from __future__ import annotations

import logging

from prismclaw.engine.integrations.mcp.adapter import MCPToolAdapter
from prismclaw.engine.integrations.mcp.settings import McpServerConfig

from prismclaw.engine.integrations.mcp._mcp_base import _MCPBackendBase

logger = logging.getLogger(__name__)


class AdapterMixin(_MCPBackendBase):
    def _build_adapters_for_session(
        self,
        server_name: str,
        config: McpServerConfig,
        session,
    ) -> list[MCPToolAdapter]:
        adapters: list[MCPToolAdapter] = []
        for sdk_key, tool in self._group.tools.items():
            if self._group._tool_to_session.get(sdk_key) is not session:
                continue
            try:
                adapter = MCPToolAdapter(
                    server_name=server_name,
                    remote_tool=tool,
                    server_config=config,
                    group=self._group,
                    sdk_key=sdk_key,
                    runtime_observer=self,
                )
            except ValueError as exc:
                logger.warning(
                    "MCP tool %r:%r rejected: %s. Skipping this tool only.",
                    server_name,
                    tool.name,
                    exc,
                )
                continue
            adapters.append(adapter)
        return adapters
