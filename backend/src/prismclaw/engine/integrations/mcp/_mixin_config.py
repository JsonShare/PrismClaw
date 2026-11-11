"""Auto-extracted Mixin from client_manager.py — DO NOT edit method signatures."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Literal

from prismclaw.engine.integrations.mcp._types import (
    MCPRuntimeTelemetry,
    ServerStatus,
)
from prismclaw.engine.integrations.mcp.settings import McpServerConfig

from prismclaw.engine.integrations.mcp._mcp_base import _MCPBackendBase

logger = logging.getLogger(__name__)


class ConfigMixin(_MCPBackendBase):
    def set_sandbox_policy(self, sandbox_policy: Any) -> None:
        """Inject the resolved ``SandboxPolicy`` from app lifespan."""
        if sandbox_policy is None:
            raise RuntimeError(
                "MCPClientManager.set_sandbox_policy requires a resolved sandbox_policy"
            )
        self._sandbox_policy = sandbox_policy

    def _require_sandbox_policy(self) -> Any:
        if self._sandbox_policy is None:
            raise RuntimeError(
                "MCPClientManager requires set_sandbox_policy() before startup when MCP is enabled"
            )
        return self._sandbox_policy

    @property
    def sandbox_backend(self) -> str:
        return getattr(self._sandbox_policy, "backend", "none")

    @property
    def sandbox_policy(self) -> Any:
        return self._sandbox_policy

    def _emit_sandbox_startup_advisories(self) -> None:
        if self._sandbox_advisories_emitted:
            return
        self._sandbox_advisories_emitted = True

        from prismclaw.engine.integrations.mcp.settings import _command_auto_exempts_sandbox

        for name, config in self._settings.servers.items():
            if not config.enabled:
                continue
            if config.transport != "stdio":
                logger.info(
                    "MCP server %r: transport=%s (HTTP-based transports currently bypass local sandbox wrapping)",
                    name,
                    config.transport,
                )
                continue
            if not config.sandbox.enabled:
                if _command_auto_exempts_sandbox(config.command):
                    logger.info(
                        "MCP server %r: command=%r auto-exempted from sandbox "
                        "(npx/uvx registry probe incompatible). For full "
                        "isolation use a local binary path + sandbox.enabled=true. "
                        "See docs/zh/sandbox.md.",
                        name,
                        config.command,
                    )
                continue
            logger.warning(
                "MCP server %r: sandbox.enabled=true (Sprint 3 default). "
                "Verify policy and per-server overrides; see docs/zh/sandbox.md.",
                name,
            )

    def _initialize_server_statuses(self) -> None:
        for name, config in self._settings.servers.items():
            self._runtime_telemetry[name] = MCPRuntimeTelemetry(
                forced_chat_only=getattr(config, "forced_chat_only", False),
            )
            if not config.enabled:
                self._servers[name] = ServerStatus(name=name, status="disabled")
            else:
                self._servers[name] = ServerStatus(name=name, status="pending")

    def _telemetry_for(self, name: str) -> MCPRuntimeTelemetry:
        telemetry = self._runtime_telemetry.get(name)
        if telemetry is None:
            config = self._settings.servers.get(name)
            telemetry = MCPRuntimeTelemetry(
                forced_chat_only=getattr(config, "forced_chat_only", False),
            )
            self._runtime_telemetry[name] = telemetry
        return telemetry

    def _status_with_runtime(self, status: ServerStatus) -> ServerStatus:
        return replace(status, runtime=replace(self._telemetry_for(status.name)))

    def note_tool_decision(
        self,
        server_name: str,
        *,
        tool_name: str,
        decision: Literal["approved", "denied"],
        reason: str | None = None,
    ) -> None:
        telemetry = self._telemetry_for(server_name)
        now = datetime.now(timezone.utc)
        telemetry.last_tool_name = tool_name
        telemetry.last_tool_decision_at = now
        telemetry.last_tool_decision = decision
        telemetry.last_tool_decision_reason = reason
        if decision == "denied":
            telemetry.total_denied_calls += 1

    def note_tool_result(
        self,
        server_name: str,
        *,
        tool_name: str,
        outcome: Literal["success", "error", "timeout", "server_dead"],
        detail: str | None = None,
    ) -> None:
        telemetry = self._telemetry_for(server_name)
        now = datetime.now(timezone.utc)
        telemetry.last_tool_name = tool_name
        telemetry.last_tool_call_at = now
        telemetry.total_tool_calls += 1
        if outcome == "success":
            telemetry.last_tool_success_at = now
            telemetry.last_tool_error = None
            return
        telemetry.total_failed_calls += 1
        telemetry.last_tool_failure_at = now
        telemetry.last_tool_error = detail
        if outcome == "timeout":
            telemetry.total_timed_out_calls += 1

    def _enabled_servers(self) -> list[tuple[str, McpServerConfig]]:
        return [
            (name, config)
            for name, config in self._settings.servers.items()
            if config.enabled
        ]

    def _get_lock(self, name: str) -> asyncio.Lock:
        if name not in self._locks:
            self._locks[name] = asyncio.Lock()
        return self._locks[name]
