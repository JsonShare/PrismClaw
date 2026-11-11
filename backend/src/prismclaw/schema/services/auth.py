"""Auth sub-contracts: UserProfile data class and UserProfileStore protocol."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

Channel = Literal["web"]


@dataclass(frozen=True)
class UserProfile:
    channel: Channel
    user_id: str
    tenant_id: str | None = None
    unknown_fields: dict[str, Any] | None = None


class UserProfileStore(Protocol):
    async def get(self, channel: str, user_id: str) -> UserProfile: ...

    async def set(
        self, profile: UserProfile, *, ttl_seconds: int | None = None
    ) -> bool: ...

    async def list_users(
        self, channel: str
    ) -> list[UserProfile]: ...

    async def discard(self, channel: str, user_id: str) -> bool: ...
