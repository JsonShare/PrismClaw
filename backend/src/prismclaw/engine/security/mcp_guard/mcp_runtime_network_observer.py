"""§11.3 McpRuntimeNetworkObserver — MCP 运行时网络行为观察。

比较 MCP 声明的 runtime_network_expect 与实际代理流量，
3-strike 自动拉黑违规 MCP 服务器。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class NetworkViolationKind(str, Enum):
    UNDECLARED_DOMAIN = "undeclared_domain"
    UNDECLARED_PORT = "undeclared_port"
    UNDECLARED_PROTOCOL = "undeclared_protocol"


@dataclass
class NetworkViolation:
    server_name: str
    kind: NetworkViolationKind
    detail: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class McpNetworkBlacklistEntry:
    server_name: str
    reason: str
    blacklisted_at: float
    strike_count: int


class McpRuntimeNetworkObserver:
    """MCP 运行时网络行为观察器。
    
    监控 MCP 服务器的实际网络行为，与声明的 runtime_network_expect 对比。
    3-strike 自动拉黑违规 MCP 服务器。
    """
    
    def __init__(
        self,
        *,
        max_strikes: int = 3,
        strike_window_seconds: float = 3600.0,
        blacklist_duration_seconds: float = 86400.0,
    ) -> None:
        self._max_strikes = max_strikes
        self._strike_window = strike_window_seconds
        self._blacklist_duration = blacklist_duration_seconds
        self._violations: dict[str, list[NetworkViolation]] = {}
        self._blacklist: dict[str, McpNetworkBlacklistEntry] = {}
    
    def observe_traffic(
        self,
        server_name: str,
        actual_host: str,
        actual_port: int,
        declared_domains: list[str] | None = None,
    ) -> NetworkViolation | None:
        """观察一次 MCP 网络流量，与声明对比。"""
        if self.is_blacklisted(server_name):
            return None  # Already blacklisted, no need to observe
        
        violation = None
        if declared_domains and actual_host not in declared_domains:
            violation = NetworkViolation(
                server_name=server_name,
                kind=NetworkViolationKind.UNDECLARED_DOMAIN,
                detail=f"MCP {server_name} accessed undeclared domain: {actual_host} (declared: {declared_domains})",
            )
        
        if violation:
            self._record_violation(violation)
        
        return violation
    
    def _record_violation(self, violation: NetworkViolation) -> None:
        name = violation.server_name
        if name not in self._violations:
            self._violations[name] = []
        self._violations[name].append(violation)
        
        # Prune expired violations
        cutoff = time.time() - self._strike_window
        self._violations[name] = [v for v in self._violations[name] if v.timestamp > cutoff]
        
        # Check strike count
        if len(self._violations[name]) >= self._max_strikes:
            self._blacklist[name] = McpNetworkBlacklistEntry(
                server_name=name,
                reason=f"{self._max_strikes} strikes in {self._strike_window}s window",
                blacklisted_at=time.time(),
                strike_count=len(self._violations[name]),
            )
            logger.warning(
                "McpRuntimeNetworkObserver: BLACKLISTED MCP server %s "
                "(%d strikes: %s)",
                name, len(self._violations[name]),
                [v.detail for v in self._violations[name]],
            )
    
    def is_blacklisted(self, server_name: str) -> bool:
        entry = self._blacklist.get(server_name)
        if entry is None:
            return False
        # Auto-expire blacklist entries
        if time.time() - entry.blacklisted_at > self._blacklist_duration:
            del self._blacklist[server_name]
            return False
        return True
    
    def get_strikes(self, server_name: str) -> list[NetworkViolation]:
        return self._violations.get(server_name, [])
    
    def get_blacklist(self) -> dict[str, McpNetworkBlacklistEntry]:
        return dict(self._blacklist)
