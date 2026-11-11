"""Security subsystem contracts.

Protocols for safety gate evaluation, consent management,
and policy / runtime-mode / signer data models.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field


class SecurityGateProtocol(Protocol):
    """Protocol for safety gate evaluation — duck-typing compatible with security/gate.SafetyGate."""

    async def evaluate(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        mode: str = "work",
        workspace_tag: Any | None = None,
        workspace_root: str | None = None,
        llm_risk_hint: str | None = None,
        session_id: str = "",
        user_id: str = "",
        tenant_id: str = "",
    ) -> Any:
        """Evaluate a tool call against security policies, returning a decision."""
        ...


class ConsentProtocol(Protocol):
    """Protocol for consent management — duck-typing compatible with security/consent_gate.ConsentGate."""

    def check(
        self,
        *,
        session_id: str,
        user_id: str,
        tool_name: str,
        declared_purposes: list[str],
        workspace_id: str = "",
    ) -> Any:
        """Check whether consent exists for the given tool/purpose combination."""
        ...

    def grant(
        self,
        *,
        session_id: str,
        user_id: str,
        purpose: str,
        tool_name: str,
        scope: str = "",
        workspace_id: str = "",
        ttl_seconds: int | None = None,
    ) -> Any:
        """Grant consent for a purpose/tool combination."""
        ...

    def revoke(self, consent_id: str) -> None:
        """Revoke a previously granted consent."""
        ...

    def list_active_consents(self, session_id: str, user_id: str) -> list[Any]:
        """List all active consents for a session/user."""
        ...


# ── Policy / runtime-mode data models ──────────────────────────────────────


class AllowedToolsPolicy(BaseModel):
    allow_all: bool = True
    allowed_names: tuple[str, ...] = ()
    denied_names: tuple[str, ...] = ()

    def allows(self, tool_name: str) -> bool:
        if tool_name in self.denied_names:
            return False
        if self.allow_all:
            return True
        return tool_name in self.allowed_names


class RuntimeMode(BaseModel):
    stable_prefix_mode: bool = False
    discovery_mode: bool = True
    headless_mode: bool = False
    extra_flags: dict[str, bool] = Field(default_factory=dict)


# ── Signer Protocol ────────────────────────────────────────────────────────


@runtime_checkable
class SignerProtocol(Protocol):
    """签名协议 — 支持本地 Ed25519 和 HSM PKCS#11。"""

    def sign(self, payload: bytes) -> str:
        """对 payload 签名，返回 hex 编码的签名。"""
        ...

    def verify(self, payload: bytes, signature: str, key_version: str | None = None) -> bool:
        """验证签名。key_version=None 使用当前主密钥。"""
        ...

    @property
    def key_version(self) -> str:
        """当前密钥版本。"""
        ...

    @property
    def public_key_hex(self) -> str:
        """当前公钥（hex 编码）。"""
        ...
