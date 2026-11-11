from __future__ import annotations

import re

WORKSPACE_ID_ALLOWED_RE = re.compile(r"^[a-zA-Z0-9_\-\u4e00-\u9fa5]+$")


class InvalidWorkspaceIdError(ValueError):
    """Raised when a workspace id violates the shared Web contract."""


def clean_workspace_id(workspace_id: str | None) -> str:
    if workspace_id is None:
        return "default"
    raw = workspace_id.strip()
    if not raw:
        return "default"
    if raw == "default":
        return "default"
    if len(raw) > 64 or not WORKSPACE_ID_ALLOWED_RE.fullmatch(raw):
        raise InvalidWorkspaceIdError("Invalid workspace_id")
    return raw
