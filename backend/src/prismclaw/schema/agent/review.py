"""Review service contracts.

Protocol for document review/apply operations, and content-addressable
storage types (ContentRef / ContentStore).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


class ReviewServiceProtocol(Protocol):
    """Protocol for review operations — duck-typing compatible with engine.services.review_service."""

    async def apply_review(
        self,
        session_id: str,
        user_id: str,
        review_id: str,
        *,
        changes: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Apply a review to the workspace."""
        ...

    async def get_review_status(
        self,
        session_id: str,
        review_id: str,
    ) -> dict[str, Any] | None:
        """Get the current status of a review."""
        ...


# ── Content-addressable storage ─────────────────────────────────────────────


@dataclass(frozen=True)
class ContentRef:
    """Immutable pointer to stored content. ``ref`` is the legacy path form
    ``blobs/<sha256hex>``; ``sha256`` is the canonical content hash."""

    sha256: str
    ref: str


@runtime_checkable
class ContentStore(Protocol):
    """Port for content storage. Integrity + lifecycle are part of the contract,
    not external checks bolted on afterwards."""

    def put(self, data: bytes) -> ContentRef: ...
    def get(self, ref: ContentRef) -> bytes: ...
    def exists(self, ref: ContentRef) -> bool: ...
