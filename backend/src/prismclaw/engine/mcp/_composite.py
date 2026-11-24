from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Literal, Protocol

import mcp

from prismclaw.engine.mcp._types import (
    ConnectionSummary,
    RestartResult,
    ServerStatus,
)
from prismclaw.schema.shared.exceptions import MCPServerDeadError

if TYPE_CHECKING:
    from prismclaw.engine.mcp._utils import _ToolRegistryProto


class _MCPManagerProto(Protocol):
    """CompositeMCPManager 聚合所需的子管理器契约。

    ``MCPClientManager`` 与 ``DeclarativeMCPManager`` 都满足；后者只是状态占位，
    不真正连接，因此这里是结构化协议而非某个具体实现类型。
    """

    def attach_and_register(self, registry: _ToolRegistryProto) -> None: ...
    def list_servers(self) -> list[ServerStatus]: ...
    def get_status(self, name: str) -> ServerStatus | None: ...
    def connection_summary(self) -> ConnectionSummary: ...
    def is_ready(self) -> bool: ...
    async def wait_ready(self) -> None: ...
    async def restart_server(self, name: str) -> RestartResult: ...
    def get_logs(self, name: str) -> str: ...
    def get_session(self, name: str) -> mcp.ClientSession | None: ...
    def note_tool_decision(
        self,
        server_name: str,
        *,
        tool_name: str,
        decision: Literal["approved", "denied"],
        reason: str | None = None,
    ) -> None: ...
    def note_tool_result(
        self,
        server_name: str,
        *,
        tool_name: str,
        outcome: Literal["success", "error", "timeout", "server_dead"],
        detail: str | None = None,
    ) -> None: ...
    async def shutdown(self) -> None: ...


class CompositeMCPManager:
    def __init__(self, managers: Sequence[_MCPManagerProto]) -> None:
        self._managers: list[_MCPManagerProto] = list(managers)

    def _find_manager(self, server_name: str) -> _MCPManagerProto | None:
        for manager in self._managers:
            for status in manager.list_servers():
                if status.name == server_name:
                    return manager
        return None

    def attach_and_register(self, registry: _ToolRegistryProto) -> None:
        for manager in self._managers:
            manager.attach_and_register(registry)

    def list_servers(self) -> list[ServerStatus]:
        statuses: list[ServerStatus] = []
        for manager in self._managers:
            statuses.extend(manager.list_servers())
        return statuses

    def get_status(self, name: str) -> ServerStatus | None:
        for manager in self._managers:
            status = manager.get_status(name)
            if status is not None:
                return status
        return None

    def connection_summary(self) -> ConnectionSummary:
        summary = ConnectionSummary()
        for manager in self._managers:
            child = manager.connection_summary()
            summary.n_connected += child.n_connected
            summary.n_failed += child.n_failed
            summary.n_pending += child.n_pending
            summary.n_disabled += child.n_disabled
            summary.total_tools += child.total_tools
        return summary

    def is_ready(self) -> bool:
        return all(manager.is_ready() for manager in self._managers)

    async def wait_ready(self) -> None:
        for manager in self._managers:
            waiter = getattr(manager, "wait_ready", None)
            if callable(waiter):
                await waiter()

    async def restart_server(self, name: str) -> RestartResult:
        manager = self._find_manager(name)
        if manager is None:
            return RestartResult(ok=False, reason=f"server {name!r} not configured")
        return await manager.restart_server(name)

    def get_logs(self, name: str) -> str:
        manager = self._find_manager(name)
        if manager is None:
            return ""
        return manager.get_logs(name)

    def get_session(self, name: str) -> mcp.ClientSession | None:
        manager = self._find_manager(name)
        if manager is None:
            return None
        return manager.get_session(name)

    def list_mcp_prompts(self) -> list[tuple[str, Any]]:
        """Aggregate ``(server_name, Prompt)`` pairs across all child managers."""
        result: list[tuple[str, Any]] = []
        for manager in self._managers:
            method = getattr(manager, "list_mcp_prompts", None)
            if callable(method):
                result.extend(method())
        return result

    def get_prompt_text_threadsafe(
        self,
        server_name: str,
        prompt_name: str,
        arguments: dict[str, str] | None = None,
        *,
        timeout_s: float = 15.0,
    ) -> str:
        manager = self._find_manager(server_name)
        fetch = getattr(manager, "get_prompt_text_threadsafe", None)
        if not callable(fetch):
            raise MCPServerDeadError(
                server_name,
                f"cannot fetch prompt {prompt_name!r}: manager does not support prompts",
            )
        return str(fetch(server_name, prompt_name, arguments, timeout_s=timeout_s))

    def note_tool_decision(
        self,
        server_name: str,
        *,
        tool_name: str,
        decision: Literal["approved", "denied"],
        reason: str | None = None,
    ) -> None:
        manager = self._find_manager(server_name)
        if manager is None:
            return
        note = getattr(manager, "note_tool_decision", None)
        if callable(note):
            note(server_name, tool_name=tool_name, decision=decision, reason=reason)

    def note_tool_result(
        self,
        server_name: str,
        *,
        tool_name: str,
        outcome: Literal["success", "error", "timeout", "server_dead"],
        detail: str | None = None,
    ) -> None:
        manager = self._find_manager(server_name)
        if manager is None:
            return
        note = getattr(manager, "note_tool_result", None)
        if callable(note):
            note(server_name, tool_name=tool_name, outcome=outcome, detail=detail)

    async def shutdown(self) -> None:
        seen: set[int] = set()
        for manager in self._managers:
            manager_id = id(manager)
            if manager_id in seen:
                continue
            seen.add(manager_id)
            await manager.shutdown()
