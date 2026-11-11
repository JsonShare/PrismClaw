"""Upload service contracts.

Protocol for file upload handling.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


class UploadServiceProtocol(Protocol):
    """Protocol for upload operations — duck-typing compatible with engine.services.upload_service."""

    async def handle_upload(
        self,
        session_id: str,
        user_id: str,
        *,
        filename: str,
        content: bytes,
        workspace_base: Path | None = None,
    ) -> dict[str, Any]:
        """Handle a file upload, returning metadata about the stored file."""
        ...

    async def list_uploads(
        self,
        session_id: str,
        user_id: str,
    ) -> list[dict[str, Any]]:
        """List uploaded files for a session."""
        ...
