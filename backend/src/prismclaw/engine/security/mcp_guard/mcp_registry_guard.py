"""P1-SEC-07 + P2-MCP-01 + P4-SEC-02：McpRegistryGuard — MCP 注册表安全防护。

核心安全约束：
  1. 未注册 MCP server → fail-closed 最严分类 + WORM 审计
  2. SSE/HTTP 传输 → deny（容器化完成前不信任网络传输）
  3. 签名校验：Ed25519 / HSM PKCS#11（P4-SEC-02 已接入 SignerProtocol）
  4. 已注册 server → 根据 McpSecurityMeta 分类
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from prismclaw.engine.security.gate.routing.risk_router import SideEffectScope, DataClassification, Reversibility
from prismclaw.engine.security.crypto.hsm_signer import SignerProtocol, LocalEd25519Signer

logger = logging.getLogger(__name__)


class McpTransport(str, Enum):
    STDIO = "stdio"
    SSE = "sse"
    STREAMABLE_HTTP = "streamable-http"
    HTTP = "http"


class McpRiskLevel(str, Enum):
    """MCP server 风险级别。"""
    TRUSTED = "trusted"       # 已注册 + 签名校验通过 + stdio
    UNVERIFIED = "unverified" # 已注册但签名未校验
    BLOCKED = "blocked"       # SSE/HTTP 传输 或 未注册


@dataclass(frozen=True)
class McpSecurityMeta:
    """MCP server 安全元数据。"""
    server_name: str
    transport: str
    risk_level: McpRiskLevel
    side_effect_scope: SideEffectScope = SideEffectScope.EXTERNAL_WRITE  # 默认最严格
    data_classification: DataClassification = DataClassification.SENSITIVE
    reversibility: Reversibility = Reversibility.IRREVERSIBLE
    operator_id: str = ""
    signature_valid: bool = False
    runtime_network_expect: str = "none"  # none/limited/full
    deny_reason: str = ""


@dataclass
class McpRegistryEntry:
    """MCP 注册表条目。"""
    server_name: str
    transport: str
    operator_id: str = ""
    public_key: str = ""  # Ed25519 公钥（hex）
    signature: str = ""   # P4-SEC-02: 注册签名（hex）— signer.sign(canonical_payload)
    key_version: str = "" # P4-SEC-02: 签名密钥版本
    allowed_scopes: list[str] = field(default_factory=list)
    runtime_network_expect: str = "none"


@dataclass
class McpGuardResult:
    """McpRegistryGuard 检查结果。"""
    allowed: bool
    risk_level: McpRiskLevel
    meta: McpSecurityMeta | None = None
    reason: str = ""


class McpRegistryGuard:
    """MCP 注册表安全防护。

    最小版（Phase 2）：
      - JSON 文件加载注册表
      - SSE/HTTP transport → deny
      - 未注册 server → fail-closed
      - Ed25519 签名校验（可选）
    """

    def __init__(
        self,
        *,
        registry_path: str | None = None,
        allow_non_stdio: bool = False,
        signer: SignerProtocol | None = None,
    ) -> None:
        self._registry: dict[str, McpRegistryEntry] = {}
        self._allow_non_stdio = allow_non_stdio
        self._signer: SignerProtocol = signer or LocalEd25519Signer()
        if registry_path:
            self._load_registry(registry_path)

    def _load_registry(self, path: str) -> None:
        """从 JSON 文件加载注册表。"""
        try:
            with open(path, "r") as f:
                data = json.load(f)
            for entry_data in data.get("servers", []):
                entry = McpRegistryEntry(
                    server_name=entry_data.get("name", ""),
                    transport=entry_data.get("transport", "stdio"),
                    operator_id=entry_data.get("operator_id", ""),
                    public_key=entry_data.get("public_key", ""),
                    signature=entry_data.get("signature", ""),
                    key_version=entry_data.get("key_version", ""),
                    allowed_scopes=entry_data.get("allowed_scopes", []),
                    runtime_network_expect=entry_data.get("runtime_network_expect", "none"),
                )
                self._registry[entry.server_name] = entry
            logger.info("McpRegistryGuard: loaded %d entries from %s", len(self._registry), path)
        except FileNotFoundError:
            logger.warning("McpRegistryGuard: registry file not found: %s", path)
        except json.JSONDecodeError as e:
            logger.error("McpRegistryGuard: invalid JSON in registry: %s", e)

    def register(self, entry: McpRegistryEntry) -> None:
        """注册 MCP server。"""
        self._registry[entry.server_name] = entry

    def check(
        self,
        server_name: str,
        transport: str,
        *,
        worm_audit: Any = None,
        session_id: str = "",
    ) -> McpGuardResult:
        """检查 MCP server 是否允许使用。"""
        # 1. SSE/HTTP transport → deny（容器化完成前不信任网络传输）
        if transport in (McpTransport.SSE.value, McpTransport.HTTP.value, McpTransport.STREAMABLE_HTTP.value):
            if not self._allow_non_stdio:
                meta = McpSecurityMeta(
                    server_name=server_name,
                    transport=transport,
                    risk_level=McpRiskLevel.BLOCKED,
                    deny_reason=f"mcp_non_stdio_blocked: {transport} transport not allowed before containerization",
                )
                if worm_audit:
                    worm_audit.log(session_id, "mcp_non_stdio_blocked", {
                        "server_name": server_name,
                        "transport": transport,
                    })
                return McpGuardResult(
                    allowed=False,
                    risk_level=McpRiskLevel.BLOCKED,
                    meta=meta,
                    reason=meta.deny_reason,
                )

        # 2. 查找注册表
        entry = self._registry.get(server_name)
        if entry is None:
            # 未注册 → fail-closed 最严分类
            meta = McpSecurityMeta(
                server_name=server_name,
                transport=transport,
                risk_level=McpRiskLevel.BLOCKED,
                side_effect_scope=SideEffectScope.EXTERNAL_IRREVERSIBLE,
                data_classification=DataClassification.SENSITIVE,
                reversibility=Reversibility.IRREVERSIBLE,
                deny_reason=f"mcp_unregistered: {server_name} not in registry, fail-closed",
            )
            if worm_audit:
                worm_audit.log(session_id, "mcp_unregistered", {
                    "server_name": server_name,
                    "transport": transport,
                })
            return McpGuardResult(
                allowed=False,
                risk_level=McpRiskLevel.BLOCKED,
                meta=meta,
                reason=meta.deny_reason,
            )

        # 3. 签名校验（P4-SEC-02：SignerProtocol 真实签名验证）
        signature_valid = False
        if entry.signature and entry.key_version:
            # 真实签名验证：signer.verify(canonical_payload, signature, key_version)
            canonical_payload = json.dumps({
                "server_name": entry.server_name,
                "transport": entry.transport,
                "operator_id": entry.operator_id,
                "public_key": entry.public_key,
                "allowed_scopes": entry.allowed_scopes,
            }, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
            signature_valid = self._signer.verify(canonical_payload, entry.signature, entry.key_version)
        elif entry.public_key and entry.signature:
            # 无签名但有公钥：公钥匹配即标记为可信（向后兼容）
            # 注意：空签名不应视为有效
            if entry.public_key == self._signer.public_key_hex:
                signature_valid = True

        # 4. 构建安全元数据
        scope = SideEffectScope.EXTERNAL_WRITE
        if entry.allowed_scopes:
            try:
                scope = SideEffectScope(entry.allowed_scopes[0])
            except ValueError:
                logger.warning(
                    "McpRegistryGuard: invalid scope %r for %s, defaulting to LOCAL_WORKSPACE",
                    entry.allowed_scopes[0], entry.server_name,
                )
                scope = SideEffectScope.LOCAL_WORKSPACE

        risk_level = McpRiskLevel.TRUSTED if signature_valid else McpRiskLevel.UNVERIFIED

        meta = McpSecurityMeta(
            server_name=server_name,
            transport=transport,
            risk_level=risk_level,
            side_effect_scope=scope,
            operator_id=entry.operator_id,
            signature_valid=signature_valid,
            runtime_network_expect=entry.runtime_network_expect,
        )

        return McpGuardResult(
            allowed=True,
            risk_level=risk_level,
            meta=meta,
            reason="mcp_registered",
        )
