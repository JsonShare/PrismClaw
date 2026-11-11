"""P1-RUN-01：WorkspaceRuntimeClient — HTTP transport + HMAC 签名。

WorkspaceRuntimeClient 是工具与 Runtime-Agent 之间的通信层。
工具不再直接调用 `asyncio.create_subprocess_exec` 或 `Path.read_text()`，
而是通过此客户端发送 HTTP 请求到 Runtime-Agent，由 Agent 在容器内执行。

架构：
  Tool → WorkspaceRuntimeClient.bash("ls", tier=STANDARD) 
       → HMAC 签名 + HTTP POST 
       → Runtime-Agent /bash 
       → BwrapPolicy 执行 
       → CommandResult

安全：
  - 每个请求带 HMAC-SHA256 签名（v{key_version}:sha256-hmac-body）
  - 密钥从环境变量 / Vault 动态拉取
  - 90 天轮换 + 7 天共存窗口
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
from typing import Any, Tuple

import httpx


logger = logging.getLogger(__name__)


# ── UTF-8 字节安全切片（用于 write_stream 文本模式字节分片） ─────────────


def _cut_utf8_safe(data: bytes, max_bytes: int) -> Tuple[bytes, str]:
    """返回 data 中不超过 max_bytes 的**最长合法 UTF-8 前缀**。

    Args:
        data: UTF-8 字节串（通常是一大段文本编码后完整内容）
        max_bytes: 允许读取的最大字节数

    Returns:
        (safe_bytes, decoded_str)：safe_bytes 是 data 前缀，保证合法；
        decoded_str 是其对应的文本。
    """
    if max_bytes <= 0:
        return b"", ""
    if max_bytes >= len(data):
        return data, data.decode("utf-8")

    # 从边界向前扫描最多 6 字节（理论上 UTF-8 最长 4B，留余量），
    # 找到「合法字符起始位置」才算安全切割点。
    #   合法起始：
    #     0xxxxxxx               (ASCII, 1B)   → 下一位 pos+1 安全
    #     110xxxxx + 1× 10xxxxxx  (2B)   → pos+2 <= max_bytes 安全
    #     1110xxxx + 2× 10xxxxxx  (3B)   → pos+3 <= max_bytes 安全
    #     11110xxx + 3× 10xxxxxx  (4B)   → pos+4 <= max_bytes 安全
    scan_from = max(0, max_bytes - 6)
    for pos in range(max_bytes, scan_from - 1, -1):
        b = data[pos] if pos < len(data) else 0
        if (b & 0x80) == 0:                       # 1B ASCII start
            cut = pos + 1
            break
        if (b & 0xE0) == 0xC0 and pos + 2 <= max_bytes:  # 2B start
            cut = pos
            break
        if (b & 0xF0) == 0xE0 and pos + 3 <= max_bytes:  # 3B start
            cut = pos
            break
        if (b & 0xF8) == 0xF0 and pos + 4 <= max_bytes:  # 4B start
            cut = pos
            break
    else:
        # 回退到 scan_from 之前也没找到（极小概率），退化为 0 长度
        cut = 0

    chunk = data[:cut]
    try:
        s = chunk.decode("utf-8")
    except UnicodeDecodeError:
        # 绝对兜底：逐字 errors="replace" + 返回原始 max_bytes 切片
        chunk = data[:max_bytes]
        s = chunk.decode("utf-8", errors="replace")
    return chunk, s


# ── 数据类型 ──────────────────────────────────────────────────────────

from prismclaw.schema.services.env_types import CommandResult, FileResult


# ── HMAC 签名 ──────────────────────────────────────────────────────────


def _sign_payload(body: bytes, secret: str, key_version: str) -> str:
    """HMAC-SHA256 签名，格式：v{key_version}:sha256-hmac-body"""
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"v{key_version}:{sig}"


# ── WorkspaceRuntimeClient ─────────────────────────────────────────────


class WorkspaceRuntimeClient:
    """工具与 Runtime-Agent 之间的 HTTP 通信层。

    每个工具调用都通过此客户端发送到 Runtime-Agent，
    由 Agent 在容器内安全执行。
    """

    def __init__(
        self,
        *,
        agent_urls: dict[str, str] | None = None,
        hmac_secret: str | None = None,
        hmac_key_version: str = "1",
        workspace_id: str = "",
        timeout_seconds: float = 30.0,
    ) -> None:
        # P10-SEC-04: template_id → agent_url 路由表（dict[kind] = url）。
        self._agent_urls: dict[str, str] = dict(agent_urls or {})
        # 环境变量补全：PRISMCLAW_RUNTIME_AGENT_URLS（JSON 映射）。
        if not self._agent_urls:
            env_map = os.getenv("PRISMCLAW_RUNTIME_AGENT_URLS")
            if env_map:
                import json as _json

                try:
                    self._agent_urls.update(_json.loads(env_map))
                except (_json.JSONDecodeError, TypeError):
                    logger.warning("PRISMCLAW_RUNTIME_AGENT_URLS is not valid JSON: %r", env_map)
        if not self._agent_urls:
            self._agent_urls["__default__"] = "http://localhost:9001"
        self._hmac_secret = hmac_secret or os.getenv("PRISMCLAW_RUNTIME_HMAC_SECRET", "dev-secret")
        self._hmac_key_version = hmac_key_version
        self._workspace_id = workspace_id
        self._timeout = timeout_seconds
        self._client: httpx.AsyncClient | None = None
        self._clients: dict[str, httpx.AsyncClient] = {}

    def _resolve_agent_url(self, template_id: str) -> str:
        """按 template_id 解析目标 Runtime-Agent URL。

        解析优先级：
          1. 精确匹配 template_id（如 "restricted-v1"）
          2. 回退到 "__default__"
          3. 再回退到映射中第一个值（避免彻底无路由）
        """
        if template_id in self._agent_urls:
            return self._agent_urls[template_id]
        if "__default__" in self._agent_urls:
            return self._agent_urls["__default__"]
        return next(iter(self._agent_urls.values()))

    async def _ensure_client(self, template_id: str = "__default__") -> httpx.AsyncClient:
        url = self._resolve_agent_url(template_id)
        client = self._clients.get(url)
        if client is None or client.is_closed:
            client = httpx.AsyncClient(
                base_url=url,
                timeout=httpx.Timeout(self._timeout),
            )
            self._clients[url] = client
        return client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    def _sign(self, body: bytes) -> str:
        return _sign_payload(body, self._hmac_secret, self._hmac_key_version)

    async def _post(
        self,
        endpoint: str,
        payload: dict[str, Any],
        template_id: str = "__default__",
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        """发送签名 HTTP POST 到指定 template_id 的 Runtime-Agent。

        workspace_id 优先使用调用级传入值，否则回退到实例级 _workspace_id。
        服务端会校验 X-Workspace-ID 与本容器 WORKSPACE_ID 一致（隔离边界）。
        """
        client = await self._ensure_client(template_id)
        body = json.dumps(payload).encode()
        signature = self._sign(body)
        resp = await client.post(
            endpoint,
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Workspace-ID": workspace_id or self._workspace_id,
                "X-Template-ID": template_id,
                "X-HMAC-Signature": signature,
            },
        )
        resp.raise_for_status()
        return resp.json()

    async def _get(
        self,
        endpoint: str,
        template_id: str = "__default__",
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        """发送签名 HTTP GET 到 Runtime-Agent（如 /bash/jobs）。

        GET 无请求体，HMAC 以空字节签名（服务端中间件对 GET 取空 body 校验）。
        """
        client = await self._ensure_client(template_id)
        signature = self._sign(b"")
        resp = await client.get(
            endpoint,
            headers={
                "X-Workspace-ID": workspace_id or self._workspace_id,
                "X-Template-ID": template_id,
                "X-HMAC-Signature": signature,
            },
        )
        resp.raise_for_status()
        return resp.json()

    # ── 公共 API ──────────────────────────────────────────────────────

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
        """在容器内执行 bash 命令。

        返回 ``CommandResult``（同步完成）或 detached dict
        （超时 detach 为后台任务：``{"detached": True, "job_id": pid, "pid": pid}``）。
        """
        soft = soft_timeout if soft_timeout is not None else (timeout_seconds or 30)
        payload: dict[str, Any] = {
            "command": command,
            "tier": str(tier),
            "soft_timeout": int(soft),
        }
        if hard_timeout is not None:
            payload["hard_timeout"] = int(hard_timeout)
        if workspace_root is not None:
            payload["workspace_root"] = workspace_root

        data = await self._post("/bash", payload, template_id=template_id, workspace_id=workspace_id)
        if data.get("detached"):
            return data
        return CommandResult(
            exit_code=data.get("exit_code", -1),
            stdout=data.get("stdout", ""),
            stderr=data.get("stderr", ""),
            timed_out=data.get("timed_out", False),
            sandbox_tier_used=data.get("tier_used", str(tier)),
        )

    async def read_text(self, path: str, *, encoding: str = "utf-8", template_id: str = "__default__", workspace_id: str | None = None, workspace_root: str | None = None) -> FileResult:
        """在容器内读取文本文件。"""
        data = await self._post("/read", {"path": path, "mode": "text", "encoding": encoding}, template_id=template_id, workspace_id=workspace_id)
        if data.get("success"):
            return FileResult(success=True, content=data.get("content", ""))
        return FileResult(success=False, error=data.get("error", "unknown error"))

    async def write_text(self, path: str, content: str, *, encoding: str = "utf-8", template_id: str = "__default__", workspace_id: str | None = None, workspace_root: str | None = None) -> FileResult:
        """在容器内写入文本文件。"""
        data = await self._post("/write", {"path": path, "content": content, "mode": "text", "encoding": encoding}, template_id=template_id, workspace_id=workspace_id)
        return FileResult(success=data.get("success", False), error=data.get("error"))

    async def read_bytes(self, path: str, *, template_id: str = "__default__", workspace_id: str | None = None, workspace_root: str | None = None) -> FileResult:
        """在容器内读取二进制文件。"""
        data = await self._post("/read", {"path": path, "mode": "bytes"}, template_id=template_id, workspace_id=workspace_id)
        if data.get("success"):
            return FileResult(success=True, content=base64.b64decode(data.get("content_b64", "")))
        return FileResult(success=False, error=data.get("error", "unknown error"))

    async def write_bytes(self, path: str, content: bytes, *, template_id: str = "__default__", workspace_id: str | None = None, workspace_root: str | None = None) -> FileResult:
        """在容器内写入二进制文件。"""
        data = await self._post("/write", {
            "path": path,
            "content_b64": base64.b64encode(content).decode(),
            "mode": "bytes",
        }, template_id=template_id, workspace_id=workspace_id)
        return FileResult(success=data.get("success", False), error=data.get("error"))

    async def write_stream(
        self,
        path: str,
        content: str | bytes,
        *,
        mode: str = "text",
        encoding: str = "utf-8",
        chunk_size: int = 512 * 1024,  # 512KB 分片（字节）
        template_id: str = "__default__",
        workspace_id: str | None = None,
        workspace_root: str | None = None,
    ) -> FileResult:
        """P4-TOOL-01：分片流式写入文件（替代 base64 heredoc 大文件写入）。

        协议：begin → chunk[] → finish
        性能：10MB 文件从 ~5s（base64 heredoc）降至 ~1s。

        关键实现：
          - **两种模式统一按字节分片**：chunk_size 是字节数不是字符数。
            否则 CJK（3B/char）场景下「512KB 分片」实际会膨胀到 1.5MB+，
            分片大小不可控，也与服务端 total_bytes 计数不一致。
          - text 模式：先把 content 编码成 bytes（保留 encoding 用于 server 端写出
            到文件时的文本解码），客户端分片传输统一用 base64 + 字节分片，
            server 端 chunk handler 再按对应模式写回。
          - bytes 模式：内容已是 bytes，直接 base64 分片传输即可。

        Args:
            path: 目标文件路径
            content: 写入内容（str 或 bytes）
            mode: "text" 或 "bytes"（决定 server 端如何写出到文件）
            encoding: server 端文本写回时的编码（也用于将 str→bytes 分片）
            chunk_size: 分片大小（字节，不是字符数）
        """
        if mode not in ("text", "bytes"):
            return FileResult(success=False, error=f"invalid mode: {mode}")

        # 统一规范化为 bytes 以便按字节切片
        if mode == "bytes":
            if isinstance(content, str):
                payload = content.encode(encoding)
            else:
                payload = content
            send_mode = "bytes"
        else:  # text
            text = content if isinstance(content, str) else content.decode(encoding)
            payload = text.encode(encoding)
            send_mode = "text"

        # begin（告知 server 按 text/bytes 模式写出，encoding 也一并传递）
        begin_data = await self._post("/write_stream", {
            "action": "begin",
            "path": path,
            "mode": send_mode,
            "encoding": encoding,
        }, template_id=template_id, workspace_id=workspace_id)
        if not begin_data.get("success"):
            return FileResult(success=False, error=begin_data.get("error", "begin failed"))
        session_id = begin_data["session_id"]

        try:
            total = len(payload)
            offset = 0
            while offset < total:
                if send_mode == "bytes":
                    chunk_bytes = payload[offset:offset + chunk_size]
                    send_data = base64.b64encode(chunk_bytes).decode("ascii")
                else:
                    # text 模式：字节边界安全切割 → 返回合法 (bytes, str)
                    remaining = payload[offset:]
                    chunk_bytes, send_data = _cut_utf8_safe(remaining, chunk_size)
                    if len(chunk_bytes) == 0:
                        # 理论上不应触发（cut_utf8_safe 最坏返回空），兜底整段处理
                        chunk_bytes = remaining
                        send_data = remaining.decode(encoding, errors="replace")
                chunk_data = await self._post("/write_stream", {
                    "action": "chunk",
                    "session_id": session_id,
                    "data": send_data,
                }, template_id=template_id, workspace_id=workspace_id)
                if not chunk_data.get("success"):
                    return FileResult(success=False, error=chunk_data.get("error", "chunk failed"))
                offset += len(chunk_bytes)

            # finish
            finish_data = await self._post("/write_stream", {
                "action": "finish",
                "session_id": session_id,
            }, template_id=template_id, workspace_id=workspace_id)
            return FileResult(success=finish_data.get("success", False), error=finish_data.get("error"))
        except Exception as e:
            # 尝试清理会话
            try:
                await self._post("/write_stream", {"action": "finish", "session_id": session_id}, template_id=template_id, workspace_id=workspace_id)
            except Exception:
                pass
            return FileResult(success=False, error=str(e))

    async def http_get(self, url: str, *, tier: str = "standard", template_id: str = "__default__", workspace_id: str | None = None, workspace_root: str | None = None) -> FileResult:
        """通过容器内代理获取 HTTP 资源。"""
        data = await self._post("/http_get", {"url": url, "tier": str(tier)}, template_id=template_id, workspace_id=workspace_id)
        if data.get("success"):
            return FileResult(success=True, content=data.get("content", ""), http_status_code=data.get("http_status_code"), http_headers=data.get("http_headers"))
        return FileResult(success=False, error=data.get("error", "unknown error"), http_status_code=data.get("http_status_code"), http_headers=data.get("http_headers"))

    async def fs_list(self, path: str = ".", *, recursive: bool = False, ignore_hidden: bool = True, template_id: str = "__default__", workspace_id: str | None = None, workspace_root: str | None = None) -> dict[str, Any]:
        """列出容器内目录内容。"""
        payload = {"path": path, "recursive": recursive, "ignore_hidden": ignore_hidden}
        return await self._post("/fs/list", payload, template_id, workspace_id)

    async def fs_glob(self, pattern: str = "*", path: str = ".", *, max_results: int = 200, template_id: str = "__default__", workspace_id: str | None = None, workspace_root: str | None = None) -> dict[str, Any]:
        """在容器内按 glob 模式搜索文件。"""
        payload = {"pattern": pattern, "path": path, "max_results": max_results}
        return await self._post("/fs/glob", payload, template_id, workspace_id)

    async def fs_grep(self, pattern: str, path: str = ".", *, include: str = "*", case_insensitive: bool = False, max_matches: int = 200, template_id: str = "__default__", workspace_id: str | None = None, workspace_root: str | None = None) -> dict[str, Any]:
        """在容器内按正则模式搜索文件内容。"""
        payload = {"pattern": pattern, "path": path, "include": include, "case_insensitive": case_insensitive, "max_matches": max_matches}
        return await self._post("/fs/grep", payload, template_id, workspace_id)

    async def http_post(self, url: str, *, body: Any = None, headers: dict[str, str] | None = None, content_type: str = "application/json", tier: str = "standard", template_id: str = "__default__", workspace_id: str | None = None, workspace_root: str | None = None) -> FileResult:
        """通过容器内代理发送 HTTP POST 请求。"""
        payload: dict[str, Any] = {"url": url, "tier": str(tier)}
        if body is not None:
            payload["body"] = body
        if headers is not None:
            payload["headers"] = headers
        payload["content_type"] = content_type
        resp = await self._post("/http_post", payload, template_id, workspace_id)
        if resp.get("success"):
            return FileResult(
                success=True,
                content=resp.get("content", ""),
                http_status_code=resp.get("http_status_code"),
                http_headers=resp.get("http_headers"),
            )
        return FileResult(
            success=False,
            error=resp.get("error", "unknown error"),
            http_status_code=resp.get("http_status_code"),
            http_headers=resp.get("http_headers"),
        )

    async def health(self, template_id: str = "__default__", workspace_id: str | None = None, workspace_root: str | None = None) -> bool:
        """检查指定 template_id 的 Runtime-Agent 是否健康。"""
        try:
            client = await self._ensure_client(template_id)
            resp = await client.get("/health")
            return resp.status_code == 200
        except Exception:
            return False

    async def mcp_spawn(self, server_name: str, *, transport: str = "stdio", args: list[str] | None = None, env: dict[str, str] | None = None, template_id: str = "__default__", workspace_id: str | None = None, workspace_root: str | None = None) -> dict:
        """在容器内启动 MCP 服务器。"""
        return await self._post("/mcp/spawn", {"server_name": server_name, "transport": transport, "args": args or [], "env": env or {}}, template_id=template_id, workspace_id=workspace_id)

    async def mcp_call(self, server_name: str, tool_name: str, arguments: dict | None = None, template_id: str = "__default__", workspace_id: str | None = None, workspace_root: str | None = None) -> dict:
        """调用容器内 MCP 服务器工具。"""
        return await self._post("/mcp/call", {"server_name": server_name, "tool_name": tool_name, "arguments": arguments or {}}, template_id=template_id, workspace_id=workspace_id)

    # ── 归档 ──────────────────────────────────────────────────────────

    async def archive_create(self, name: str, *, template_id: str = "__default__", workspace_id: str | None = None, workspace_root: str | None = None) -> dict[str, Any]:
        payload = {"name": name}
        return await self._post("/archive/create", payload, template_id, workspace_id)

    async def archive_restore(self, archive_id: str, *, template_id: str = "__default__", workspace_id: str | None = None, workspace_root: str | None = None) -> dict[str, Any]:
        payload = {"archive_id": archive_id}
        return await self._post("/archive/restore", payload, template_id, workspace_id)

    async def archive_list(self, *, template_id: str = "__default__", workspace_id: str | None = None, workspace_root: str | None = None) -> dict[str, Any]:
        return await self._get("/archive/list", template_id, workspace_id)

    # ── 后台任务（与生产 server.py 端点一致）─────────────────────────────

    async def list_jobs(
        self, template_id: str = "__default__", workspace_id: str | None = None, workspace_root: str | None = None
    ) -> list[dict[str, Any]]:
        """列出工作区后台任务。"""
        data = await self._get("/bash/jobs", template_id=template_id)
        return data.get("jobs", [])

    async def job_status(
        self, pid: int, *, template_id: str = "__default__", workspace_id: str | None = None, workspace_root: str | None = None
    ) -> dict[str, Any] | None:
        """查询后台任务状态；不存在返回 None。"""
        try:
            return await self._get(f"/bash/jobs/{pid}", template_id=template_id)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise

    async def job_kill(
        self, pid: int, *, template_id: str = "__default__", workspace_id: str | None = None, workspace_root: str | None = None
    ) -> bool:
        """杀掉后台任务，成功返回 True。"""
        data = await self._post(
            f"/bash/jobs/{pid}/kill", {}, template_id=template_id
        )
        return bool(data.get("killed", False))
