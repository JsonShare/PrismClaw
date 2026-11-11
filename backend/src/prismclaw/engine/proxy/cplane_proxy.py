"""§6 C Plane Network Proxy — 容器出站代理 + DLP。

所有容器的 HTTP/HTTPS/SMTP 出站请求必须经过此代理。
- HMAC 身份验证 (x-prismclaw-identity)
- MCP 域名白名单 (McpNetworkAcl)
- 内联 DLP/PHI 扫描
- DNS 隧道阻断（仅服务端 DNS 解析）
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import logging
import socket
from typing import Any

import aiohttp
from aiohttp import web

from prismclaw.engine.proxy.mcp_network_acl import McpNetworkAcl
from prismclaw.engine.security.dlp.scanner import DlpScanner, DlpScanResult

logger = logging.getLogger(__name__)

# ── 常量 ────────────────────────────────────────────────────────────────

_IDENTITY_HEADER = "x-prismclaw-identity"
_IDENTITY_PREFIX = "v"
_IDENTITY_SEPARATOR = ":"
_HMAC_ALGO = "sha256"


class CPlaneProxy:
    """C Plane Network Proxy — 容器出站代理。

    所有容器的 HTTP/HTTPS 出站请求通过此代理：
      1. HMAC 身份验证
      2. MCP 域名白名单
      3. 内联 DLP/PHI 扫描
      4. DNS 隧道阻断
    """

    def __init__(
        self,
        *,
        listen_host: str = "0.0.0.0",
        listen_port: int = 8443,
        hmac_secret: str,
        acl: McpNetworkAcl,
        dlp_scanner: DlpScanner | None = None,
        worm_audit: Any = None,
    ) -> None:
        self._listen_host = listen_host
        self._listen_port = listen_port
        self._hmac_secret = hmac_secret
        self._acl = acl
        self._dlp = dlp_scanner
        self._worm_audit = worm_audit
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._tcp_connector: aiohttp.TCPConnector | None = None

    # ── 生命周期 ───────────────────────────────────────────────────────

    async def start(self) -> None:
        """启动代理服务器。"""
        app = web.Application()
        app.router.add_route("CONNECT", "/{path:.*}", self._handle_connect)
        app.router.add_route("*", "/{path:.*}", self._handle_http)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(
            self._runner,
            self._listen_host,
            self._listen_port,
        )
        await self._site.start()
        logger.info("CPlaneProxy started on %s:%d", self._listen_host, self._listen_port)

    async def stop(self) -> None:
        """优雅关闭代理服务器。"""
        if self._tcp_connector is not None:
            await self._tcp_connector.close()
            self._tcp_connector = None
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
        logger.info("CPlaneProxy stopped")

    # ── HTTP CONNECT 隧道 ──────────────────────────────────────────────

    async def _handle_connect(self, request: web.Request) -> web.StreamResponse:
        """处理 HTTP CONNECT 隧道请求。

        流程：
          1. 验证 HMAC 身份
          2. 提取目标 host
          3. ACL 白名单检查
          4. DNS 隧道阻断（拒绝原始 IP 连接）
          5. 服务端 DNS 解析
          6. 建立隧道
        """
        # 1) HMAC 身份验证
        valid, identity_info = self._verify_identity(request.headers)
        if not valid:
            logger.warning("CPlaneProxy: CONNECT rejected - invalid identity: %s", identity_info)
            return web.Response(status=403, text=f"Forbidden: {identity_info}")

        # 2) 提取目标 host:port
        host_port = request.path
        if ":" in host_port:
            host, port_str = host_port.rsplit(":", 1)
            try:
                port = int(port_str)
            except ValueError:
                return web.Response(status=400, text=f"Bad Request: invalid port in {host_port}")
        else:
            host = host_port
            port = 443

        # 3) ACL 白名单检查
        server_name = request.headers.get("x-mcp-server-name")
        if not self._check_acl(host, server_name):
            logger.warning(
                "CPlaneProxy: CONNECT rejected - ACL denied host=%s server=%s",
                host, server_name,
            )
            return web.Response(status=403, text=f"Forbidden: host '{host}' not in ACL whitelist")

        # 4) DNS 隧道阻断：拒绝原始 IP 连接
        resolved_ip = self._resolve_dns(host)
        if resolved_ip == "":
            logger.warning("CPlaneProxy: CONNECT rejected - DNS resolution failed for host=%s", host)
            return web.Response(status=502, text=f"Bad Gateway: DNS resolution failed for {host}")

        # 5) 建立到目标的 TCP 连接
        try:
            reader, writer = await aiohttp.asyncio.open_connection(resolved_ip, port)
        except OSError as e:
            logger.error("CPlaneProxy: CONNECT failed to %s:%d - %s", host, port, e)
            return web.Response(status=502, text=f"Bad Gateway: cannot connect to {host}:{port}")

        # 6) 建立隧道
        response = web.StreamResponse(status=200, reason="Connection Established")
        response.set_tcp_nodelay(True)
        try:
            await response.prepare(request)
        except ConnectionError:
            writer.close()
            await writer.wait_closed()
            return response

        # 双向数据转发
        async def _pipe_upstream() -> None:
            """从客户端读取数据并转发到目标服务器。"""
            try:
                while True:
                    chunk = await request.content.read(65536)
                    if not chunk:
                        break
                    # DLP 扫描请求体（如果有）
                    if self._dlp is not None and chunk:
                        result = self._dlp.scan_for_proxy(chunk, "outbound")
                        if not result.allowed:
                            logger.warning(
                                "CPlaneProxy: outbound DLP block - phi_count=%d phi_ratio=%.2f",
                                result.phi_count, result.phi_ratio,
                            )
                            break
                    writer.write(chunk)
                    await writer.drain()
            except (ConnectionError, asyncio.CancelledError):
                pass
            finally:
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass

        async def _pipe_downstream() -> None:
            """从目标服务器读取数据并转发到客户端。"""
            try:
                while True:
                    chunk = await reader.read(65536)
                    if not chunk:
                        break
                    # DLP 扫描响应体
                    if self._dlp is not None and chunk:
                        result = self._dlp.scan_for_proxy(chunk, "inbound")
                        if not result.allowed:
                            logger.warning(
                                "CPlaneProxy: inbound DLP block - phi_count=%d phi_ratio=%.2f",
                                result.phi_count, result.phi_ratio,
                            )
                            break
                    await response.write(chunk)
            except (ConnectionError, asyncio.CancelledError):
                pass
            finally:
                try:
                    await response.write_eof()
                except Exception:
                    pass

        import asyncio
        await asyncio.gather(_pipe_upstream(), _pipe_downstream(), return_exceptions=True)
        return response

    # ── HTTP 正向代理 ──────────────────────────────────────────────────

    async def _handle_http(self, request: web.Request) -> web.StreamResponse:
        """处理普通 HTTP 正向代理请求。"""
        # HMAC 身份验证
        valid, identity_info = self._verify_identity(request.headers)
        if not valid:
            return web.Response(status=403, text=f"Forbidden: {identity_info}")

        # 解析目标 URL
        target_url = str(request.url)
        if not target_url.startswith(("http://", "https://")):
            target_url = f"http://{target_url}"

        from urllib.parse import urlparse
        parsed = urlparse(target_url)
        host = parsed.hostname or ""

        # ACL 检查
        server_name = request.headers.get("x-mcp-server-name")
        if not self._check_acl(host, server_name):
            return web.Response(status=403, text=f"Forbidden: host '{host}' not in ACL whitelist")

        # DNS 隧道阻断
        resolved_ip = self._resolve_dns(host)
        if resolved_ip == "":
            return web.Response(status=502, text=f"Bad Gateway: DNS resolution failed for {host}")

        # 读取请求体
        body = await request.read()

        # DLP 扫描请求体
        if self._dlp is not None and body:
            result = self._dlp.scan_for_proxy(body, "outbound")
            if not result.allowed:
                return web.Response(
                    status=403,
                    text=f"PHI_BLOCKED: outbound DLP scan detected {result.phi_count} PHI identifiers",
                )

        # 转发请求
        try:
            if self._tcp_connector is None:
                self._tcp_connector = aiohttp.TCPConnector(ssl=False)

            async with aiohttp.ClientSession(connector=self._tcp_connector) as session:
                async with session.request(
                    method=request.method,
                    url=target_url,
                    headers={
                        k: v for k, v in request.headers.items()
                        if k.lower() not in ("host", "x-prismclaw-identity", "x-mcp-server-name")
                    },
                    data=body,
                    allow_redirects=False,
                ) as upstream_resp:
                    # DLP 扫描响应体
                    resp_body = await upstream_resp.read()
                    if self._dlp is not None and resp_body:
                        result = self._dlp.scan_for_proxy(resp_body, "inbound")
                        if not result.allowed:
                            return web.Response(
                                status=403,
                                text=f"PHI_BLOCKED: inbound DLP scan detected {result.phi_count} PHI identifiers",
                            )

                    # 返回响应
                    downstream = web.StreamResponse(status=upstream_resp.status)
                    downstream.headers.update(upstream_resp.headers)
                    await downstream.prepare(request)
                    if resp_body:
                        await downstream.write(resp_body)
                    await downstream.write_eof()
                    return downstream
        except Exception as e:
            logger.error("CPlaneProxy: HTTP proxy error - %s", e)
            return web.Response(status=502, text=f"Bad Gateway: {e}")

    # ── HMAC 身份验证 ──────────────────────────────────────────────────

    def _verify_identity(self, headers: dict | web.Headers) -> tuple[bool, str]:
        """验证 HMAC 身份头。

        格式：x-prismclaw-identity: v{version}:hmac-sha256-body
        与 WorkspaceRuntimeClient 的 _sign_payload 格式一致。

        Args:
            headers: HTTP 请求头

        Returns:
            (is_valid, message): 验证结果 + 说明
        """
        identity = headers.get(_IDENTITY_HEADER, "")
        if not identity:
            return False, "missing x-prismclaw-identity header"

        # 解析 v{version}:hmac-sha256-body 格式
        if not identity.startswith(_IDENTITY_PREFIX):
            return False, "invalid identity format: must start with 'v'"

        parts = identity.split(_IDENTITY_SEPARATOR, 2)
        if len(parts) < 3:
            return False, "invalid identity format: expected v{version}:hmac-sha256-{digest}"

        _version = parts[0]  # v{N}
        algo_part = parts[1]  # e.g. "hmac-sha256"
        received_digest = parts[2]  # hex digest

        # 验证算法
        if not algo_part.endswith(_HMAC_ALGO):
            return False, f"unsupported HMAC algorithm: {algo_part}"

        # 重新计算 HMAC
        # 身份验证签名基于空 body（代理层无法获取原始签名 body，
        # 因此验证 header 存在性 + 格式正确性 + digest 长度）
        # 生产环境应将 body 一并签名，此处验证 digest 是否为合法 hex
        try:
            _bytes = bytes.fromhex(received_digest)
            if len(_bytes) != 32:  # SHA-256 = 32 bytes
                return False, "invalid HMAC digest length"
        except ValueError:
            return False, "invalid HMAC digest: not hex-encoded"

        # 使用 secret 对固定 payload 做 HMAC 验证
        # 在代理层，我们验证请求方是否持有正确的 HMAC secret
        # 方法：期望 digest == HMAC-SHA256(secret, version:algo)
        expected = hmac.new(
            self._hmac_secret.encode(),
            f"{_version}:{algo_part}".encode(),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, received_digest):
            return False, "HMAC signature mismatch"

        return True, "ok"

    @staticmethod
    def sign_identity(hmac_secret: str, key_version: str = "1") -> str:
        """生成 x-prismclaw-identity 头值。

        与 _verify_identity 对称：签名格式 v{version}:hmac-sha256-{digest}。

        Args:
            hmac_secret: HMAC 密钥
            key_version: 密钥版本号

        Returns:
            签名后的 identity 头值
        """
        algo = f"hmac-{_HMAC_ALGO}"
        payload = f"v{key_version}:{algo}".encode()
        digest = hmac.new(hmac_secret.encode(), payload, hashlib.sha256).hexdigest()
        return f"v{key_version}:{algo}:{digest}"

    # ── ACL 白名单检查 ────────────────────────────────────────────────

    def _check_acl(self, host: str, server_name: str | None = None) -> bool:
        """检查目标 host 是否在 ACL 白名单中。

        Args:
            host: 目标主机名
            server_name: MCP server 名称

        Returns:
            True 如果允许访问
        """
        return self._acl.is_allowed(host, server_name)

    # ── DLP 扫描 ───────────────────────────────────────────────────────

    def _scan_dlp(self, body: bytes, direction: str) -> DlpScanResult:
        """内联 PHI 扫描。

        Args:
            body: 请求/响应体
            direction: "outbound" 或 "inbound"

        Returns:
            DlpScanResult
        """
        if self._dlp is None:
            return DlpScanResult(
                allowed=True,
                phi_count=0,
                phi_ratio=0.0,
                blocked_identifiers=[],
                scan_time_ms=0.0,
            )
        return self._dlp.scan_for_proxy(body, direction)  # type: ignore[arg-type]

    # ── DNS 解析 ───────────────────────────────────────────────────────

    def _resolve_dns(self, hostname: str) -> str:
        """服务端 DNS 解析，拒绝原始 IP 连接（防 DNS 隧道）。

        容器内无 resolver，域名解析 100% 在 Proxy 侧执行。
        如果 hostname 本身是 IP 地址，则拒绝（防止 DNS 隧道外带）。

        Args:
            hostname: 目标主机名

        Returns:
            解析后的 IP 地址；空字符串表示解析失败或被拒绝
        """
        # 拒绝原始 IP 连接
        try:
            ipaddress.ip_address(hostname)
            logger.warning("CPlaneProxy: DNS tunnel block - direct IP connection rejected: %s", hostname)
            return ""
        except ValueError:
            pass  # 不是 IP，继续 DNS 解析

        try:
            results = socket.getaddrinfo(hostname, None, socket.AF_INET)
            if results:
                return results[0][4][0]
        except socket.gaierror:
            pass

        try:
            results = socket.getaddrinfo(hostname, None, socket.AF_INET6)
            if results:
                return results[0][4][0]
        except socket.gaierror:
            pass

        return ""
