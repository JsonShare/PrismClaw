"""Security store Protocols.

Low-level store/backend Protocols for security subsystems:
aggregate counters, break-glass tokens, confirmation channel,
and consent persistence.

These are distinct from the higher-level ConsentProtocol contract
in ``security.py`` — they describe *storage* contracts, not orchestration.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from prismclaw.engine.security.consent.consent_gate import ConsentRecord


@runtime_checkable
class ConsentStoreProtocol(Protocol):
    """ConsentGate 声明驱动 — 工具需声明用途，用户同意。"""

    def record_consent(
        self, session_id: str, purpose: str, tool_name: str, granted: bool, *, consent_id: str = ""
    ) -> None: ...
    def has_consent(self, session_id: str, purpose: str, tool_name: str) -> bool: ...

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
        granted: bool = True,
        consent_id: str = "",
    ) -> ConsentRecord: ...

    def revoke(self, consent_id: str) -> None: ...

    @staticmethod
    def generate_consent_id() -> str:
        return f"consent-{uuid.uuid4().hex[:12]}"

    def list_active(self, session_id: str, user_id: str) -> list[ConsentRecord]: ...


@runtime_checkable
class AggregateStoreProtocol(Protocol):
    """SessionAggregateGuard — workspace 会话内重复/循环操作计数。"""

    def increment(self, session_id: str, counter: str, delta: int = 1) -> int: ...
    def get(self, session_id: str, counter: str) -> int: ...
    def reset(self, session_id: str) -> None: ...
    def check_and_incr(
        self, session_id: str, counter: str, threshold: int, window_seconds: int, delta: int = 1
    ) -> tuple[int, bool]:
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
