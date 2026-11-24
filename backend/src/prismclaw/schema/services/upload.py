"""Upload service contracts.

Protocol for file upload handling.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


class UploadServiceProtocol(Protocol):
    """上传操作协议；与 engine.services.upload_service 鸭子类型兼容。"""

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
