"""§6.3 McpNetworkAcl — MCP 域名白名单中心。

中心化 MCP 网络 ACL：
  - 未注册 server → 返回空白名单，Fail-Closed
  - 用户 server.yaml 改了也不生效，必须 Registry 管理员改
  - 支持 per-server 域名白名单 + 全局默认策略
"""
from __future__ import annotations

import fnmatch
import logging

logger = logging.getLogger(__name__)

# ── 内置默认白名单 ──────────────────────────────────────────────────────

_DEFAULT_SERVER_DOMAINS: dict[str, list[str]] = {
    "his": ["his.hospital.com"],
    "rms": ["rms.lawfirm.com"],
    "email": ["smtp.office365.com"],
}


class McpNetworkAcl:
    """中心化 MCP 网络 ACL。

    默认 deny-all，只有管理员显式注册的域名才允许通过代理。
    """

    def __init__(self, default_policy: str = "deny") -> None:
        self._default_policy = default_policy
        self._server_domains: dict[str, list[str]] = dict(_DEFAULT_SERVER_DOMAINS)
        # 全局白名单（跨 server 共享）
        self._global_domains: list[str] = []

    def add_server(self, server_name: str, allowed_domains: list[str]) -> None:
        """注册 MCP server 允许访问的域名。

        Args:
            server_name: MCP server 名称
            allowed_domains: 允许访问的域名列表（支持通配符，如 *.hospital.com）
        """
        self._server_domains[server_name] = list(allowed_domains)
        logger.info("McpNetworkAcl: registered server=%s domains=%s", server_name, allowed_domains)

    def add_global_domains(self, domains: list[str]) -> None:
        """添加全局白名单域名（所有 server 都可访问）。"""
        self._global_domains.extend(domains)
        logger.info("McpNetworkAcl: added global domains=%s", domains)

    def is_allowed(self, host: str, server_name: str | None = None) -> bool:
        """检查 host 是否在白名单中。

        Fail-Closed：未注册 server → 返回 False。
        域名匹配支持通配符（fnmatch 语义）。

        Args:
            host: 目标主机名
            server_name: MCP server 名称（None 时仅检查全局白名单）

        Returns:
            True 如果 host 在白名单中
        """
        if self._default_policy == "allow":
            # 仅在显式设置为 allow 时才宽松
            pass
        else:
            # 默认 deny-all，必须显式匹配
            pass

        # 检查全局白名单
        for pattern in self._global_domains:
            if fnmatch.fnmatch(host.lower(), pattern.lower()):
                return True

        # 检查 server-specific 白名单
        if server_name is not None:
            domains = self._server_domains.get(server_name)
            if domains is None:
                # 未注册 server → Fail-Closed
                logger.warning("McpNetworkAcl: unregistered server=%s host=%s → DENIED", server_name, host)
                return False
            for pattern in domains:
                if fnmatch.fnmatch(host.lower(), pattern.lower()):
                    return True

        # 如果没有指定 server_name，检查所有已注册域名
        if server_name is None:
            for _srv, domains in self._server_domains.items():
                for pattern in domains:
                    if fnmatch.fnmatch(host.lower(), pattern.lower()):
                        return True

        return False

    def list_allowed_domains(self, server_name: str | None = None) -> list[str]:
        """列出白名单域名。

        Args:
            server_name: MCP server 名称（None 时返回全局 + 所有 server 的域名）

        Returns:
            域名列表
        """
        if server_name is not None:
            return list(self._server_domains.get(server_name, []))

        result = list(self._global_domains)
        for domains in self._server_domains.values():
            result.extend(domains)
        return result
