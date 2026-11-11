"""Auto-extracted Mixin from client_manager.py — DO NOT edit method signatures."""
from __future__ import annotations

import asyncio
import logging
import tempfile
from collections import deque
from datetime import datetime, timezone

import anyio
from mcp.client.session_group import SseServerParameters, StreamableHttpParameters
from mcp.client.stdio import StdioServerParameters
from mcp.shared.exceptions import MCPError

from prismclaw.engine.integrations.mcp._types import (
    RestartResult,
    ServerStatus,
    _STDERR_BUFFER_LINES,
)
from prismclaw.engine.integrations.mcp._utils import (
    _is_unfilled_placeholder,
)
from prismclaw.engine.integrations.mcp._patch import (
    _STDIO_ERRLOG_FILE,
)
from prismclaw.engine.integrations.mcp.settings import (
    McpServerConfig,
    _substitute_env_placeholder,
)

from prismclaw.engine.integrations.mcp._mcp_base import _MCPBackendBase

logger = logging.getLogger(__name__)


class ConnectionMixin(_MCPBackendBase):
    def _server_logger(self, name: str) -> logging.Logger:
        """按需安装并返回该 server 的客户端侧文件 logger（logs/mcp_client_<name>/）。"""
        from prismclaw.infra.logging_setup import install_mcp_client_logging

        return install_mcp_client_logging(name)

    def _warn_failed(self, name: str, msg: str, *args) -> None:
        """连接失败事件：主日志 + 该 server 的 error.log 同步记录。"""
        logger.warning(msg, *args)
        try:
            self._server_logger(name).warning(msg, *args)
        except Exception:
            logger.debug("mcp server logger emit error", exc_info=True)

    def _open_stderr_capture(self, name: str):
        self._stop_stderr_pump(name)
        existing = self._stderr_files.pop(name, None)
        if existing is not None:
            try:
                existing.close()
            except Exception:
                logger.debug("mcp.stderr_capture close error", exc_info=True)
        from prismclaw.infra.logging_setup import sanitize_mcp_log_name

        safe = sanitize_mcp_log_name(name)
        # 命名临时文件：子进程 OS 级写 fd；另有 pump 任务轮询该文件逐行进 logging
        capture = tempfile.NamedTemporaryFile(
            mode="w+t", encoding="utf-8", delete=False, prefix=f"mcp_{safe}_stderr_"
        )
        self._stderr_files[name] = capture
        return capture

    def _stop_stderr_pump(self, name: str) -> None:
        task = self._stderr_pumps.pop(name, None)
        if task is not None and not task.done():
            task.cancel()

    def _start_stderr_pump(self, name: str, capture) -> None:
        """启动 stderr 轮询：把子进程写入捕获文件的新增内容逐行 emit 到
        该 server 的独立 logger（INFO 级别 → logs/mcp_<name>/log.log）。"""
        self._stop_stderr_pump(name)
        server_logger = self._server_logger(name)
        path = getattr(capture, "name", None)
        if not path or isinstance(path, int):
            return

        async def _pump() -> None:
            pos = 0
            pending = ""
            try:
                while True:
                    await asyncio.sleep(1.0)
                    try:
                        with open(path, "rb") as f:
                            f.seek(pos)
                            data = f.read()
                            pos = f.tell()
                    except FileNotFoundError:
                        continue
                    except OSError:
                        logger.debug("mcp.stderr_pump read error", exc_info=True)
                        continue
                    if not data:
                        continue
                    text = pending + data.decode("utf-8", errors="replace")
                    *lines, pending = text.split("\n")
                    for line in lines:
                        line = line.strip()
                        if line:
                            server_logger.info("%s", line)
                    while len(pending) > 8192:  # 超长无换行，防积压
                        server_logger.info("%s", pending[:8192])
                        pending = pending[8192:]
            except asyncio.CancelledError:
                pass
            finally:
                tail = pending.strip()
                if tail:
                    server_logger.info("%s", tail)

        self._stderr_pumps[name] = asyncio.ensure_future(_pump())

    def _read_stderr_tail(self, name: str, limit: int = 600) -> str:
        """Best-effort tail of the child process's captured stderr.

        Spawning failures surface as a bare "Connection closed" without any
        hint of WHY the child died; the stderr capture usually holds the
        actual error (old node version, missing module, readonly db, ...).
        """
        capture = self._stderr_files.get(name)
        if capture is None:
            return ""
        try:
            pos = capture.tell()
            capture.seek(0)
            text = capture.read() or ""
            capture.seek(pos)
        except Exception:
            return ""
        text = text.strip()
        if not text:
            return ""
        tail = text[-limit:].replace("\n", " | ")
        return f" [stderr: {tail}]"

    @staticmethod
    def _failure_reason(exc_type: str, exc: BaseException, stderr_tail: str) -> str:
        reason = f"{exc_type}: {exc}"
        if stderr_tail:
            reason = (reason + stderr_tail)[:900]
        return reason

    async def _connect_one(self, name: str, config: McpServerConfig) -> None:
        async with self._get_lock(name):
            self._stderr_buffers.setdefault(name, deque(maxlen=_STDERR_BUFFER_LINES))

            # SDD context-governance W7-4: marketplace plugins ship env
            # template placeholders like "<在本地填入 EHR FHIR 基础 URL>".
            # Spawning those processes just produces "Connection closed"
            # warnings every startup (13× on this install). Detect the
            # placeholder BEFORE spawning and mark the server disabled —
            # a clean, expected state, not a failure. Deleting the plugin
            # config would be wrong (breaks users who fill the values).
            for key, raw_value in config.env.items():
                if _is_unfilled_placeholder(raw_value):
                    self._servers[name] = ServerStatus(
                        name=name,
                        status="disabled",
                        reason=f"环境变量 {key!r} 仍是未填写的占位符——请在插件配置中填写后再启用该服务",
                    )
                    logger.info(
                        "MCP server %r disabled: env %r is an unfilled placeholder",
                        name, key,
                    )
                    return

            resolved_env: dict[str, str] = {}
            for key, raw_value in config.env.items():
                resolved, status = _substitute_env_placeholder(raw_value)
                if status == "missing-env-var":
                    self._servers[name] = ServerStatus(
                        name=name,
                        status="failed",
                        reason=f"{key!r} 引用的环境变量未设置",
                    )
                    self._warn_failed(
                        name,
                        "MCP server %r failed: env var referenced in %r is not set",
                        name,
                        key,
                    )
                    return
                resolved_env[key] = resolved

            resolved_headers: dict[str, str] = {}
            for key, raw_value in config.headers.items():
                resolved, status = _substitute_env_placeholder(raw_value)
                if status == "missing-env-var":
                    self._servers[name] = ServerStatus(
                        name=name,
                        status="failed",
                        reason=f"请求头 {key!r} 引用的环境变量未设置",
                    )
                    self._warn_failed(
                        name,
                        "MCP server %r failed: env var referenced in header %r is not set",
                        name,
                        key,
                    )
                    return
                resolved_headers[key] = resolved

            resolved_args: list[str] = []
            for idx, raw_value in enumerate(config.args):
                resolved, status = _substitute_env_placeholder(raw_value)
                if status == "missing-env-var":
                    self._servers[name] = ServerStatus(
                        name=name,
                        status="failed",
                        reason=f"args[{idx}] 引用的环境变量未设置",
                    )
                    self._warn_failed(
                        name,
                        "MCP server %r failed: env var referenced in args[%d] is not set",
                        name,
                        idx,
                    )
                    return
                resolved_args.append(resolved or "")

            if config.transport == "stdio":
                # 为自建 server 注入日志目录与 logkit 路径（第三方 server 会忽略这些变量）。
                # PRISMCLAW_LOG_DIR=off（如容器）时客户端不落盘，server 侧同样跳过保持一致。
                from pathlib import Path as _Path

                from prismclaw.infra.logging_setup import _resolve_log_dir, sanitize_mcp_log_name
                from prismclaw.infra.paths import resolve_project_root

                if _resolve_log_dir() is not None:
                    safe_name = sanitize_mcp_log_name(name)
                    project_root = resolve_project_root()
                    resolved_env.setdefault(
                        "PRISMCLAW_MCP_SERVER_LOG_DIR",
                        str(project_root / "logs" / f"mcp_server_{safe_name}"),
                    )
                    resolved_env.setdefault(
                        "PRISMCLAW_INFRA_PATH",
                        str(_Path(__file__).resolve().parents[3] / "infra"),
                    )
                # 工作区根：MCP server 侧的路径沙箱（所有工具的路径参数必须 resolve 后在
                # 该根下），防止 agent 传 /etc/passwd、~/.ssh/id_rsa 等任意物理路径。
                try:
                    from prismclaw.infra.settings import load_settings
                    resolved_env.setdefault(
                        "PRISMCLAW_WORKSPACES_BASE",
                        str(_Path(load_settings().workspaces.default).expanduser()),
                    )
                except Exception as e:
                    # MCP server 路径沙箱依赖此变量；缺失会导致所有文件操作被拒——
                    # 打出 warning 便于回溯，server 侧的 fail-fast 守卫会给出更明确的拒绝消息。
                    logging.warning("Failed to inject PRISMCLAW_WORKSPACES_BASE into MCP server %r: %s — path sandbox will reject all file operations", name, e)

            server_params = self._build_server_params(
                name=name,
                config=config,
                resolved_env=resolved_env,
                resolved_headers=resolved_headers,
                resolved_args=resolved_args,
            )
            if server_params is None:
                return
            if config.transport == "stdio":
                logger.info(
                    "MCP stdio spawn %r: command=%r args=%r env_keys=%s",
                    name,
                    config.command,
                    resolved_args,
                    sorted(resolved_env.keys()) if resolved_env else "(default)",
                )

            if config.transport == "stdio" and config.sandbox.enabled:
                if self._sandbox_policy is None:
                    self._servers[name] = ServerStatus(
                        name=name,
                        status="failed",
                        reason=(
                            "sandbox.enabled=true 但当前没有可用的沙箱策略；"
                            "请确保 sandbox.enforce=True 且已安装 bwrap"
                            "（apt install bubblewrap），或设置 'sandbox.enabled': false"
                        ),
                    )
                    logger.error(
                        "MCP server %r marked failed: sandbox.enabled=true but no sandbox policy available",
                        name,
                    )
                    return
                try:
                    server_params = self._sandbox_policy.wrap_mcp_stdio(
                        server_params,
                        server_name=name,
                        sandbox_config={
                            "filesystem": config.sandbox.filesystem,
                            "network": config.sandbox.network,
                            "env_allowlist": config.sandbox.env_allowlist,
                        },
                    )
                except Exception as exc:
                    self._servers[name] = ServerStatus(
                        name=name,
                        status="failed",
                        reason=f"sandbox-config: {type(exc).__name__}: {exc}",
                    )
                    self._warn_failed(
                        name, "MCP server %r failed during sandbox wrap_mcp_stdio", name
                    )
                    return

            try:
                async with asyncio.timeout(config.connect_timeout_seconds):
                    errlog_file = self._open_stderr_capture(name)
                    # 进程一 spawn 即开始 pump：连接期间的 stderr（含崩溃堆栈）
                    # 全程进入 logs/mcp_<name>/log.log，而不是只靠失败时读尾巴
                    self._start_stderr_pump(name, errlog_file)
                    token_file = _STDIO_ERRLOG_FILE.set(errlog_file)
                    try:
                        session = await self._group.connect_to_server(server_params)
                    finally:
                        _STDIO_ERRLOG_FILE.reset(token_file)
            except (asyncio.TimeoutError, TimeoutError):
                self._servers[name] = ServerStatus(
                    name=name,
                    status="failed",
                    reason=f"connect timeout ({config.connect_timeout_seconds}s)",
                )
                self._warn_failed(
                    name,
                    "MCP server %r failed: connect timeout (%ss)",
                    name,
                    config.connect_timeout_seconds,
                )
                return
            except MCPError as exc:
                err_msg = exc.error.message if hasattr(exc, "error") else str(exc)
                if "already" in err_msg.lower() and "exist" in err_msg.lower():
                    self._servers[name] = ServerStatus(
                        name=name,
                        status="failed",
                        reason=(
                            "工具名冲突：另一个已连接的服务已提供至少一个同名远程工具"
                        ),
                    )
                else:
                    stderr_tail = self._read_stderr_tail(name)
                    self._servers[name] = ServerStatus(
                        name=name,
                        status="failed",
                        reason=self._failure_reason("MCPError", exc, stderr_tail),
                    )
                self._warn_failed(
                    name, "MCP server %r failed: %s%s", name, err_msg, self._read_stderr_tail(name)
                )
                return
            except (
                anyio.ClosedResourceError,
                anyio.BrokenResourceError,
                ConnectionError,
                OSError,
                FileNotFoundError,
            ) as exc:
                stderr_tail = self._read_stderr_tail(name)
                self._servers[name] = ServerStatus(
                    name=name,
                    status="failed",
                    reason=self._failure_reason(type(exc).__name__, exc, stderr_tail),
                )
                self._warn_failed(
                    name,
                    "MCP server %r failed: %s: %s%s",
                    name,
                    type(exc).__name__,
                    exc,
                    stderr_tail,
                )
                return
            except Exception as exc:
                stderr_tail = self._read_stderr_tail(name)
                self._servers[name] = ServerStatus(
                    name=name,
                    status="failed",
                    reason=self._failure_reason(type(exc).__name__, exc, stderr_tail),
                )
                self._warn_failed(
                    name,
                    "MCP server %r failed: %s: %s%s",
                    name,
                    type(exc).__name__,
                    exc,
                    stderr_tail,
                )
                return

            adapters = self._build_adapters_for_session(name, config, session)
            self._server_logger(name).info(
                "connected (transport=%s tools=%d)", config.transport, len(adapters)
            )
            self._adapters[name] = adapters
            self._sessions[name] = session
            if self._registry is not None:
                for adapter in adapters:
                    self._registry.register(adapter)
            tool_count = len(adapters)
            self._servers[name] = ServerStatus(
                name=name,
                status="connected",
                tool_count=tool_count,
                last_connect_at=datetime.now(timezone.utc),
            )
            level = logging.WARNING if tool_count == 0 else logging.INFO
            logger.log(
                level,
                "MCP server %r connected: %d tools%s",
                name,
                tool_count,
                "" if tool_count > 0 else " (check server config — server reports zero tools)",
            )

    def _build_server_params(
        self,
        *,
        name: str,
        config: McpServerConfig,
        resolved_env: dict[str, str],
        resolved_headers: dict[str, str],
        resolved_args: list[str],
    ):
        if config.transport == "stdio":
            return StdioServerParameters(
                command=config.command or "",
                args=resolved_args,
                env=resolved_env or None,
            )
        if config.transport == "sse":
            return SseServerParameters(
                url=config.url or "",
                headers=resolved_headers or None,
                timeout=config.connect_timeout_seconds,
                sse_read_timeout=config.call_timeout_seconds,
            )
        if config.transport in {"streamable-http", "http"}:
            return StreamableHttpParameters(
                url=config.url or "",
                headers=resolved_headers or None,
                timeout=config.connect_timeout_seconds,
                sse_read_timeout=config.call_timeout_seconds,
                terminate_on_close=True,
            )
        self._servers[name] = ServerStatus(
            name=name,
            status="failed",
            reason=f"unsupported transport: {config.transport}",
        )
        logger.warning("MCP server %r failed: unsupported transport %r", name, config.transport)
        return None

    async def restart_server(self, name: str) -> RestartResult:
        if name not in self._settings.servers:
            return RestartResult(ok=False, reason=f"server {name!r} not configured")
        config = self._settings.servers[name]
        if not config.enabled:
            return RestartResult(ok=False, reason=f"server {name!r} disabled")
        if (
            self._supervisor_task is not None
            and not self._supervisor_task.done()
            and asyncio.current_task() is not self._supervisor_task
        ):
            future: asyncio.Future[RestartResult] = asyncio.get_running_loop().create_future()
            await self._supervisor_commands.put(("restart", name, future))
            return await future
        return await self._restart_server_impl(name)

    async def _restart_server_impl(self, name: str) -> RestartResult:
        config = self._settings.servers[name]
        async with self._get_lock(name):
            old_adapters = list(self._adapters.get(name, []))
            for adapter in old_adapters:
                if self._registry is not None:
                    self._registry.unregister(adapter.name)
            self._adapters[name] = []
            # Evict the old session from the ClientSessionGroup before
            # reconnecting — otherwise the new session's tool registrations
            # collide with the stale ones and restart fails permanently.
            old_session = self._sessions.pop(name, None)
            if old_session is not None:
                try:
                    await self._group.disconnect_from_server(old_session)
                except Exception:
                    logger.debug(
                        "mcp.disconnect_from_server failed for old session on restart name=%s",
                        name,
                        exc_info=True,
                    )

        await self._connect_one(name, config)

        status = self._servers.get(name)
        if status is None or status.status != "connected":
            reason = (status.reason if status else "unknown") or "reconnect failed"
            return RestartResult(ok=False, reason=reason)
        return RestartResult(ok=True, tool_count=status.tool_count)

    async def _handle_server_death(self, name: str) -> None:
        async with self._get_lock(name):
            current = self._servers.get(name)
            if current is None or current.status == "failed":
                return
            adapters = list(self._adapters.get(name, []))
            for adapter in adapters:
                if self._registry is not None:
                    self._registry.unregister(adapter.name)
            self._adapters[name] = []
            # Evict the dead session from the ClientSessionGroup too — its
            # internal _tools/_tool_to_session tables keep tool-name entries
            # owned by the dead session, and a later restart would hit the
            # "tool name collision" branch in _connect_one (making the server
            # permanently unrecoverable).
            dead_session = self._sessions.pop(name, None)
            if dead_session is not None:
                try:
                    await self._group.disconnect_from_server(dead_session)
                except Exception:
                    # Session may already be torn down mid-crash; eviction is
                    # best-effort but must not block the failed-marking path.
                    logger.debug(
                        "mcp.disconnect_from_server failed for dead session name=%s",
                        name,
                        exc_info=True,
                    )
            self._servers[name] = ServerStatus(
                name=name,
                status="failed",
                reason="server died mid-call",
                last_connect_at=current.last_connect_at,
            )
            self.note_tool_result(
                name,
                tool_name="<server>",
                outcome="server_dead",
                detail="server died mid-call",
            )
            logger.warning("mcp.server_died name=%s", name)
