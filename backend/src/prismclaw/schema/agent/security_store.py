"""Security store Protocols.

Low-level store/backend Protocols for security subsystems:
aggregate counters, break-glass tokens, WORM audit, confirmation channel,
and consent persistence.

These are distinct from the higher-level ConsentProtocol / SecurityGateProtocol
in ``security.py`` — they describe *storage* contracts, not orchestration.
"""
from __future__ import annotations

import uuid
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ConsentStoreProtocol(Protocol):
    """ConsentGate 声明驱动 — 工具需声明用途，用户同意。"""

    def record_consent(self, session_id: str, purpose: str, tool_name: str, granted: bool, *, consent_id: str = "") -> None: ...
    def has_consent(self, session_id: str, purpose: str, tool_name: str) -> bool: ...

    def grant(
        self, *, session_id: str, user_id: str, purpose: str, tool_name: str,
        scope: str = "", workspace_id: str = "", ttl_seconds: int | None = None,
        granted: bool = True, consent_id: str = "",
    ) -> Any: ...

    def revoke(self, consent_id: str) -> None: ...

    @staticmethod
    def generate_consent_id() -> str:
        return f"consent-{uuid.uuid4().hex[:12]}"

    def list_active(self, session_id: str, user_id: str) -> list[Any]: ...


@runtime_checkable
class AggregateStoreProtocol(Protocol):
    """SessionAggregateGuard — workspace 会话内重复/循环操作计数。"""

    def increment(self, session_id: str, counter: str, delta: int = 1) -> int: ...
    def get(self, session_id: str, counter: str) -> int: ...
    def reset(self, session_id: str) -> None: ...
    def check_and_incr(self, session_id: str, counter: str, threshold: int, window_seconds: int, delta: int = 1) -> tuple[int, bool]:
        """原子递增 + 阈值检查。

        Returns (new_count, exceeded): exceeded=True 表示超过阈值。
        用于 Redis INCRBY + EXPIRE pipeline 的原子操作。
        """
        ...


@runtime_checkable
class BreakGlassStoreProtocol(Protocol):
    """Break-Glass 熔断开关 — Emergency Access 场景 (§24.4)。"""

    def create_token(self, session_id: str, reason: str, duration_seconds: int = 3600) -> str: ...
    def validate_token(self, token: str) -> dict[str, Any] | None: ...
    def revoke_token(self, token: str) -> None: ...


@runtime_checkable
class WormAuditProtocol(Protocol):
    """Write-Once-Read-Many 审计日志 (§24.6 HIPAA 164.312(b))。"""

    def log(self, session_id: str, event_type: str, payload: dict[str, Any]) -> str: ...
    def query(self, session_id: str, since: str | None = None) -> list[dict[str, Any]]: ...
    def verify_integrity(self, event_id: str) -> bool: ...


@runtime_checkable
class ConfirmationChannelProtocol(Protocol):
    """IRREVERSIBLE 操作的最终确认通道 (§4.3 L2)。"""

    def request(self, session_id: str, reason: str, payload: dict[str, Any]) -> str: ...
    def check(self, request_id: str) -> str: ...  # "approved" | "rejected" | "pending"
