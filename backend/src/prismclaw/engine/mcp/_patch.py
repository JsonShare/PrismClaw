from __future__ import annotations

import contextvars
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import TextIO

import mcp
from mcp.client._transport import TransportStreams
from mcp.client.stdio import StdioServerParameters

_STDIO_ERRLOG_FILE: contextvars.ContextVar[TextIO | None] = contextvars.ContextVar(
    "mcp_stdio_errlog_file",
    default=None,
)

_ORIGINAL_MCP_STDIO_CLIENT = mcp.stdio_client

_PATCH_INSTALLED = False


def _install_mcp_stdio_client_patch() -> None:
    """让 SDK 默认 stdio 传输把子进程 stderr 写进当前 server 的捕获文件。

    ``ClientSessionGroup.connect_to_server`` 只接受 ``StdioServerParameters``，
    没有逐 server 传 errlog 的入口；这里用 ContextVar 把当前 server 的捕获
    句柄带进 ``stdio_client``。未设置时保持 SDK 默认（写 ``sys.stderr``）。
    """
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return

    @asynccontextmanager
    async def patched_stdio_client(
        server: StdioServerParameters,
        errlog: TextIO = sys.stderr,
    ) -> AsyncGenerator[TransportStreams, None]:
        selected = _STDIO_ERRLOG_FILE.get() or errlog
        async with _ORIGINAL_MCP_STDIO_CLIENT(server, errlog=selected) as streams:
            yield streams

    mcp.stdio_client = patched_stdio_client
    _PATCH_INSTALLED = True
