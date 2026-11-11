"""Auto-extracted Mixin from client_manager.py — DO NOT edit method signatures."""
from __future__ import annotations

import asyncio
import logging

from prismclaw.engine.integrations.mcp._types import (
    _TOOL_COUNT_WARN_THRESHOLD,
)

from prismclaw.engine.integrations.mcp._mcp_base import _MCPBackendBase

logger = logging.getLogger(__name__)


class LifecycleMixin(_MCPBackendBase):
    def start_background(self) -> str | None:
        if not self._settings.enabled:
            self.ready.set()
            return None
        self._require_sandbox_policy()
        if self._supervisor_task is not None and not self._supervisor_task.done():
            return self._supervisor_task_id or "local-supervisor"
        try:
            self._loop = asyncio.get_running_loop()
            self._shutdown_requested = asyncio.Event()
            self._supervisor_commands = asyncio.Queue()
            task = asyncio.create_task(self._run_supervisor(), name="mcp:supervisor")
        except Exception as exc:
            logger.error(
                "mcp.start_background.skipped reason=%s details=%s",
                type(exc).__name__,
                exc,
            )
            self.ready.set()
            return None
        self._supervisor_task = task
        task_id = f"local-supervisor-{id(task)}"
        self._supervisor_task_id = task_id
        return task_id

    async def connect_all(self) -> None:
        if not self._settings.enabled:
            self.ready.set()
            return
        self._require_sandbox_policy()
        try:
            for name, config in self._enabled_servers():
                try:
                    await self._connect_one(name, config)
                except Exception:
                    logger.exception(
                        "MCP server %r connect_all raised unexpectedly", name
                    )
            self._log_startup_summary()
        except Exception:
            logger.exception("MCP connect_all crashed unexpectedly; marking ready anyway")
        finally:
            self.ready.set()

    async def _run_supervisor(self) -> None:
        try:
            await self.connect_all()
            while True:
                command, server_name, future = await self._supervisor_commands.get()
                if command == "shutdown":
                    break
                if command == "restart":
                    try:
                        result = await self._restart_server_impl(server_name)
                    except Exception as exc:
                        if future is not None and not future.done():
                            future.set_exception(exc)
                    else:
                        if future is not None and not future.done():
                            future.set_result(result)
        finally:
            try:
                await self._group.__aexit__(None, None, None)
            except Exception:
                logger.debug("mcp.supervisor group close error", exc_info=True)

    def _log_startup_summary(self) -> None:
        summary = self.connection_summary()
        level = (
            logging.WARNING
            if summary.total_tools > _TOOL_COUNT_WARN_THRESHOLD
            else logging.INFO
        )
        logger.log(
            level,
            "MCP startup summary: connected=%d failed=%d disabled=%d total_tools=%d",
            summary.n_connected,
            summary.n_failed,
            summary.n_disabled,
            summary.total_tools,
        )
        if summary.total_tools > _TOOL_COUNT_WARN_THRESHOLD:
            logger.warning(
                "MCP registered %d tools across %d connected servers; this may "
                "consume significant prompt budget. Consider disabling unused "
                "servers via 'enabled: false'.",
                summary.total_tools,
                summary.n_connected,
            )

    def is_ready(self) -> bool:
        return self.ready.is_set()

    async def wait_ready(self) -> None:
        """Await until every configured server reaches a terminal state.

        Mirrors :attr:`ready`; exists so callers can depend on a protocol
        method instead of reaching into the event attribute.
        """
        await self.ready.wait()

    async def shutdown(self) -> None:
        for name in list(self._stderr_pumps):
            try:
                self._stop_stderr_pump(name)
            except Exception:
                logger.debug("mcp.stderr_pump shutdown cancel error", exc_info=True)
        self._stderr_pumps.clear()
        for capture in self._stderr_files.values():
            try:
                path = getattr(capture, "name", None)
                capture.close()
                if path and not isinstance(path, int):
                    from pathlib import Path

                    Path(path).unlink(missing_ok=True)
            except Exception:
                logger.debug("mcp.stderr_capture shutdown close error", exc_info=True)
        self._stderr_files.clear()
        if self._supervisor_task is not None:
            try:
                self._shutdown_requested.set()
                self._supervisor_commands.put_nowait(("shutdown", "", None))
                if not self.ready.is_set():
                    self._supervisor_task.cancel()
                await self._supervisor_task
            except BaseException:
                logger.debug("mcp.shutdown supervisor cancel error", exc_info=True)
            finally:
                self._supervisor_task = None
                self._supervisor_task_id = None
            return
        try:
            await self._group.__aexit__(None, None, None)
        except Exception:
            logger.debug("mcp.shutdown group close error", exc_info=True)
