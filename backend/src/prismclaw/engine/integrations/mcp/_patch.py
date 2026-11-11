from __future__ import annotations

import contextvars
from typing import Any

import mcp
from mcp.client.stdio import StdioServerParameters

_STDIO_ERRLOG_FILE: contextvars.ContextVar[Any | None] = contextvars.ContextVar(
    "mcp_stdio_errlog_file",
    default=None,
)

_ORIGINAL_MCP_STDIO_CLIENT = mcp.stdio_client


def _install_mcp_stdio_client_patch() -> None:
    if getattr(mcp.stdio_client, "__prismclaw_patched__", False):
        return

    def patched_stdio_client(server_params: StdioServerParameters):
        errlog_file = _STDIO_ERRLOG_FILE.get()
        if errlog_file is None:
            return _ORIGINAL_MCP_STDIO_CLIENT(server_params)
        return _ORIGINAL_MCP_STDIO_CLIENT(server_params, errlog=errlog_file)

    setattr(patched_stdio_client, "__prismclaw_patched__", True)
    mcp.stdio_client = patched_stdio_client
