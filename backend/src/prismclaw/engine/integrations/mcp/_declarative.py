from __future__ import annotations

from typing import Any, Literal

import mcp

from prismclaw.engine.integrations.mcp._types import (
    ConnectionSummary,
    RestartResult,
    ServerStatus,
)


class DeclarativeMCPManager:
    def __init__(self, statuses: list[ServerStatus]) -> None:
        self._statuses = list(statuses)

    def attach_and_register(self, _registry: Any) -> None:
        return None

    def list_servers(self) -> list[ServerStatus]:
        return list(self._statuses)

    def get_status(self, name: str) -> ServerStatus | None:
        for status in self._statuses:
            if status.name == name:
                return status
        return None

    def connection_summary(self) -> ConnectionSummary:
        summary = ConnectionSummary()
        for status in self._statuses:
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

    def is_ready(self) -> bool:
        return True

    async def wait_ready(self) -> None:
        return None

    async def restart_server(self, name: str) -> RestartResult:
        for status in self._statuses:
            if status.name == name:
                return RestartResult(
                    ok=False,
                    reason=status.reason or "declarative-only MCP server cannot be restarted",
                )
        return RestartResult(ok=False, reason=f"server {name!r} not configured")

    def get_logs(self, _name: str) -> str:
        return ""

    def get_session(self, _name: str) -> "mcp.ClientSession | None":
        return None

    def note_tool_decision(
        self,
        _server_name: str,
        *,
        tool_name: str,
        decision: Literal["approved", "denied"],
        reason: str | None = None,
    ) -> None:
        _ = (tool_name, decision, reason)
        return None

    def note_tool_result(
        self,
        _server_name: str,
        *,
        tool_name: str,
        outcome: Literal["success", "error", "timeout", "server_dead"],
        detail: str | None = None,
    ) -> None:
        _ = (tool_name, outcome, detail)
        return None

    async def shutdown(self) -> None:
        return None
