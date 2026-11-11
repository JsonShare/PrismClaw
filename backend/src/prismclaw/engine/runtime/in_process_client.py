"""P1-RUN-02（重构）：InProcessRuntimeClient — 进程内传输。

与 :class:`WorkspaceRuntimeClient`（HTTP）实现**完全相同**的公开接口，
但把执行委托给同一份 :class:`RuntimeExecutor`，因此开发/测试环境
（不依赖 Docker）与生产容器内的执行逻辑**逐字节一致**：

  - 都走 RuleGate + BwrapPolicy；
  - 路径解析都以 workspace_root 为基准；
  - 后台任务（软超时 detach / 硬超时 kill）语义一致。

本类与 :class:`WorkspaceRuntimeClient` 唯一差别是传输形态（进程内直接调用 vs HTTP），
执行核心完全一致。

生产环境禁用：``PRISMCLAW_DEPLOYMENT_ENV=production`` 下构造即抛错，
确保线上只走 HTTP 容器路径，杜绝任何静默降级。
"""
from __future__ import annotations

import base64
import logging
import os
from pathlib import Path
from typing import Any

from prismclaw.schema.content.workspace import SandboxTier
from prismclaw.engine.runtime.agent.executor import RuntimeExecutor
from prismclaw.schema.services.env_types import CommandResult, FileResult

logger = logging.getLogger(__name__)

_DEPLOYMENT_ENV = os.getenv("PRISMCLAW_DEPLOYMENT_ENV", "development").lower()


def _assert_non_production() -> None:
    """生产环境禁用进程内传输。"""
    if _DEPLOYMENT_ENV == "production":
        raise RuntimeError(
            "InProcessRuntimeClient is disabled in production; "
            "use WorkspaceRuntimeClient (HTTP transport to Runtime-Agent)."
        )


def _resolve_fs_path(root: str, path: str) -> str:
    """Resolve a filesystem path relative to ``root``, with expanduser first.

    ``os.path.join(root, "~/.x")`` produces ``root/~/.x`` (literal ``~`` —
    not expanded), so we expand first, then only join when the input is
    genuinely relative. Mirror :func:`server._validate_path_within_workspace`
    and :class:`WorkspaceResolver.resolve_within` for consistency.
    """
    expanded = Path(path.strip()).expanduser()
    if expanded.is_absolute():
        return str(expanded)
    return str(Path(root).expanduser().resolve() / expanded)


class InProcessRuntimeClient:
    """进程内运行时客户端：委托 RuntimeExecutor，与生产共用执行核心。"""

    def __init__(
        self,
        *,
        workspace_root: str = "",
        hmac_secret: str | None = None,
        hmac_key_version: str = "1",
        workspace_id: str = "",
        timeout_seconds: float = 30.0,
        sandbox_policy: Any | None = None,
    ) -> None:
        _assert_non_production()
        # workspace_root 为空时，按调用级 workspace_root 动态解析 executor
        self._default_root = workspace_root
        self._sandbox_policy = sandbox_policy
        self._hmac_secret = hmac_secret
        self._hmac_key_version = hmac_key_version
        self._workspace_id = workspace_id
        self._timeout = timeout_seconds
        self._executors: dict[str, RuntimeExecutor] = {}

    def _executor_for(self, workspace_root: str | None) -> RuntimeExecutor:
        root = workspace_root or self._default_root or str(Path.cwd())
        exe = self._executors.get(root)
        if exe is None:
            exe = RuntimeExecutor(
                workspace_root=root,
                workspace_id=self._workspace_id,
                sandbox_policy=self._sandbox_policy,
            )
            self._executors[root] = exe
        return exe

    def _resolve_root(self, workspace_root: str | None) -> str:
        """解析 workspace 根路径，与 _executor_for 使用相同逻辑。"""
        return workspace_root or self._default_root or str(Path.cwd())

    # ── bash ──────────────────────────────────────────────────────────

    async def bash(
        self,
        command: str,
        *,
        tier: str = "standard",
        timeout_seconds: int | None = None,
        soft_timeout: int | None = None,
        hard_timeout: int | None = None,
        template_id: str = "__default__",
        workspace_id: str | None = None,
        workspace_root: str | None = None,
    ) -> CommandResult | dict[str, Any]:
        """执行 bash。返回 CommandResult（同步）或 detached dict（后台）。"""
        soft = soft_timeout if soft_timeout is not None else (timeout_seconds or 30)
        data = await self._executor_for(workspace_root).bash(
            command,
            tier=SandboxTier(tier),
            soft_timeout=int(soft),
            hard_timeout=int(hard_timeout) if hard_timeout is not None else None,
        )
        if data.get("detached"):
            return data
        return CommandResult(
            exit_code=data.get("exit_code", -1),
            stdout=data.get("stdout", ""),
            stderr=data.get("stderr", ""),
            timed_out=data.get("timed_out", False),
            sandbox_tier_used=data.get("tier_used", str(tier)),
        )

    # ── 文件读取 ──────────────────────────────────────────────────────

    async def read_text(self, path: str, *, encoding: str = "utf-8", template_id: str = "__default__", workspace_id: str | None = None, workspace_root: str | None = None) -> FileResult:
        data = await self._executor_for(workspace_root).read_text(path, encoding=encoding)
        return FileResult(success=data.get("success", False), content=data.get("content"), error=data.get("error"))

    async def read_bytes(self, path: str, *, template_id: str = "__default__", workspace_id: str | None = None, workspace_root: str | None = None) -> FileResult:
        data = await self._executor_for(workspace_root).read_bytes(path)
        content = None
        if data.get("success") and "content_b64" in data:
            content = base64.b64decode(data["content_b64"])
        return FileResult(success=data.get("success", False), content=content, error=data.get("error"))

    # ── 文件写入 ──────────────────────────────────────────────────────

    async def write_text(self, path: str, content: str, *, encoding: str = "utf-8", template_id: str = "__default__", workspace_id: str | None = None, workspace_root: str | None = None) -> FileResult:
        data = await self._executor_for(workspace_root).write_text(path, content, encoding=encoding)
        return FileResult(success=data.get("success", False), error=data.get("error"))

    async def write_bytes(self, path: str, content: bytes, *, template_id: str = "__default__", workspace_id: str | None = None, workspace_root: str | None = None) -> FileResult:
        data = await self._executor_for(workspace_root).write_bytes(path, content)
        return FileResult(success=data.get("success", False), error=data.get("error"))

    # ── 分片流式写入 ──────────────────────────────────────────────────

    async def write_stream(
        self,
        path: str,
        content: str | bytes,
        *,
        mode: str = "text",
        encoding: str = "utf-8",
        chunk_size: int = 512 * 1024,
        template_id: str = "__default__",
        workspace_id: str | None = None,
        workspace_root: str | None = None,
    ) -> FileResult:
        exe = self._executor_for(workspace_root)
        if mode == "bytes":
            payload = content if isinstance(content, bytes) else content.encode(encoding)
            send_mode = "bytes"
        else:
            text = content if isinstance(content, str) else content.decode(encoding)
            payload = text.encode(encoding)
            send_mode = "text"
        begin = await exe.write_stream_begin(path, send_mode, encoding)
        if not begin.get("success"):
            return FileResult(success=False, error=begin.get("error", "begin failed"))
        session_id = begin["session_id"]
        try:
            total = len(payload)
            offset = 0
            while offset < total:
                if send_mode == "bytes":
                    chunk = payload[offset:offset + chunk_size]
                    send = base64.b64encode(chunk).decode("ascii")
                else:
                    remaining = payload[offset:]
                    chunk = remaining[:chunk_size]
                    send = chunk.decode(encoding, errors="replace")
                res = await exe.write_stream_chunk(session_id, send)
                if not res.get("success"):
                    return FileResult(success=False, error=res.get("error", "chunk failed"))
                offset += len(chunk)
            fin = await exe.write_stream_finish(session_id)
            return FileResult(success=fin.get("success", False), error=fin.get("error"))
        except Exception as e:
            await exe.write_stream_finish(session_id)
            return FileResult(success=False, error=str(e))

    # ── http_get ──────────────────────────────────────────────────────

    async def http_get(self, url: str, *, tier: str = "standard", template_id: str = "__default__", workspace_id: str | None = None, workspace_root: str | None = None) -> FileResult:
        data = await self._executor_for(workspace_root).http_get(url, tier=SandboxTier(tier))
        return FileResult(
            success=data.get("success", False),
            content=data.get("content"),
            error=data.get("error"),
            http_status_code=data.get("http_status_code"),
            http_headers=data.get("http_headers"),
        )

    # ── 结构化文件系统端点（与 server.py /fs/* 语义一致） ─────────

    async def fs_list(self, path: str = ".", *, recursive: bool = False, ignore_hidden: bool = True, template_id: str = "__default__", workspace_id: str | None = None, workspace_root: str | None = None) -> dict[str, Any]:
        root = self._resolve_root(workspace_root)
        abs_path = _resolve_fs_path(root, path)
        if not os.path.isdir(abs_path):
            return {"entries": [], "error": f"directory not found or not accessible: {path}"}
        entries: list[dict[str, Any]] = []
        if recursive:
            for dirpath, dirnames, filenames in os.walk(abs_path):
                if ignore_hidden:
                    dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                    filenames = [f for f in filenames if not f.startswith(".")]
                rel_dir = os.path.relpath(dirpath, abs_path)
                for name in filenames:
                    full = os.path.join(dirpath, name)
                    try:
                        st = os.stat(full)
                        rel = name if rel_dir == "." else os.path.join(rel_dir, name)
                        entries.append({"name": name, "path": rel, "is_dir": False, "size": st.st_size, "modified_epoch": st.st_mtime})
                    except OSError:
                        pass
                for name in dirnames:
                    full = os.path.join(dirpath, name)
                    try:
                        st = os.stat(full)
                        rel = name if rel_dir == "." else os.path.join(rel_dir, name)
                        entries.append({"name": name, "path": rel, "is_dir": True, "size": 0, "modified_epoch": st.st_mtime})
                    except OSError:
                        pass
        else:
            try:
                with os.scandir(abs_path) as it:
                    for entry in it:
                        if ignore_hidden and entry.name.startswith("."):
                            continue
                        try:
                            st = entry.stat()
                            entries.append({"name": entry.name, "path": entry.name, "is_dir": entry.is_dir(), "size": st.st_size if not entry.is_dir() else 0, "modified_epoch": st.st_mtime})
                        except OSError:
                            pass
            except OSError:
                pass
        return {"entries": entries}

    async def fs_glob(self, pattern: str = "*", path: str = ".", *, max_results: int = 200, template_id: str = "__default__", workspace_id: str | None = None, workspace_root: str | None = None) -> dict[str, Any]:
        root = self._resolve_root(workspace_root)
        abs_path = _resolve_fs_path(root, path)
        entries: list[dict[str, Any]] = []
        try:
            for p in sorted(Path(abs_path).glob(pattern), key=lambda x: x.stat().st_mtime if x.exists() else 0, reverse=True):
                if not p.exists():
                    continue
                try:
                    st = p.stat()
                    entries.append({"path": str(p.relative_to(abs_path)), "size": st.st_size, "modified_epoch": st.st_mtime})
                except OSError:
                    continue
                if len(entries) >= max_results:
                    break
        except Exception:
            pass
        truncated = len(entries) >= max_results
        return {"entries": entries, "truncated": truncated}

    async def fs_grep(self, pattern: str, path: str = ".", *, include: str = "*", case_insensitive: bool = False, max_matches: int = 200, template_id: str = "__default__", workspace_id: str | None = None, workspace_root: str | None = None) -> dict[str, Any]:
        import re as _re
        root = self._resolve_root(workspace_root)
        abs_path = _resolve_fs_path(root, path)
        flags = _re.IGNORECASE if case_insensitive else 0
        matches: list[dict[str, Any]] = []
        try:
            for p in Path(abs_path).rglob(include):
                if not p.is_file():
                    continue
                try:
                    raw = p.read_bytes()[:8192]
                    if b"\x00" in raw:
                        continue
                    with open(p, "r", errors="replace") as f:
                        for line_no, line in enumerate(f, 1):
                            if _re.search(pattern, line, flags):
                                matches.append({"path": str(p.relative_to(abs_path)), "line_number": line_no, "line": line.rstrip("\n")})
                                if len(matches) >= max_matches:
                                    break
                except OSError:
                    continue
                if len(matches) >= max_matches:
                    break
        except Exception:
            pass
        truncated = len(matches) >= max_matches
        return {"matches": matches, "truncated": truncated}

    async def http_post(self, url: str, *, body: Any = None, headers: dict[str, str] | None = None, content_type: str = "application/json", tier: str = "standard", template_id: str = "__default__", workspace_id: str | None = None, workspace_root: str | None = None) -> FileResult:
        allow_net = getattr(self._executor_for(self._default_root), "_allow_net", True) or True
        if not allow_net:
            return FileResult(success=False, content=None, error="network_access_disabled", http_status_code=0, http_headers={})
        try:
            import httpx
            _headers = dict(headers) if headers else {}
            if content_type and "content-type" not in {k.lower() for k in _headers}:
                _headers["content-type"] = content_type
            async with httpx.AsyncClient(timeout=15) as client:
                if isinstance(body, dict):
                    resp = await client.post(url, json=body, headers=_headers)
                else:
                    resp = await client.post(url, content=body if isinstance(body, str) else None, headers=_headers)
                return FileResult(
                    success=200 <= resp.status_code < 300,
                    content=resp.text,
                    error=None if 200 <= resp.status_code < 300 else resp.reason_phrase,
                    http_status_code=resp.status_code,
                    http_headers=dict(resp.headers),
                )
        except Exception as exc:
            return FileResult(success=False, content=None, error=str(exc), http_status_code=0, http_headers={})

    # ── 后台任务 ──────────────────────────────────────────────────────

    async def list_jobs(self, template_id: str = "__default__", workspace_id: str | None = None, workspace_root: str | None = None) -> list[dict[str, Any]]:
        return self._executor_for(workspace_root).list_jobs()

    async def job_status(self, pid: int, *, template_id: str = "__default__", workspace_id: str | None = None, workspace_root: str | None = None) -> dict[str, Any] | None:
        # pid 在 OS 内唯一，跨所有 executor 查找
        for exe in self._executors.values():
            st = exe.job_status(pid)
            if st is not None:
                return st
        if self._default_root:
            return self._executor_for(self._default_root).job_status(pid)
        return None

    async def job_kill(self, pid: int, *, template_id: str = "__default__", workspace_id: str | None = None, workspace_root: str | None = None) -> bool:
        for exe in self._executors.values():
            if exe.job_kill(pid):
                return True
        return False

    # ── 归档（进程内模式不支持 Docker，均为 no-op）──────────────────

    async def archive_create(self, name: str, *, template_id: str = "__default__", workspace_id: str | None = None, workspace_root: str | None = None) -> dict[str, Any]:
        return {"archive_id": f"prismclaw/archive:{name}", "size_mb": 0, "error": "archive not supported in process mode"}

    async def archive_restore(self, archive_id: str, *, template_id: str = "__default__", workspace_id: str | None = None, workspace_root: str | None = None) -> dict[str, Any]:
        return {"restored": False, "error": "restore not supported in process mode"}

    async def archive_list(self, *, template_id: str = "__default__", workspace_id: str | None = None, workspace_root: str | None = None) -> dict[str, Any]:
        return {"archives": []}

    # ── health / mcp ─────────────────────────────────────────────────

    async def health(self, template_id: str = "__default__", workspace_id: str | None = None, workspace_root: str | None = None) -> bool:
        return True

    async def mcp_spawn(self, server_name: str, *, transport: str = "stdio", args: list[str] | None = None, env: dict[str, str] | None = None, template_id: str = "__default__", workspace_id: str | None = None, workspace_root: str | None = None) -> dict:
        return await self._executor_for(workspace_root).mcp_spawn(server_name, transport=transport, args=args, env=env)

    async def mcp_call(self, server_name: str, tool_name: str, arguments: dict | None = None, template_id: str = "__default__", workspace_id: str | None = None, workspace_root: str | None = None) -> dict:
        return await self._executor_for(workspace_root).mcp_call(server_name, tool_name, arguments)

    async def close(self) -> None:  # pragma: no cover - 进程内无需关闭
        pass
