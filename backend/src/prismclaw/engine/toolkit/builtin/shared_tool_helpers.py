"""Shared helper functions for built-in tools.

Consolidates utility functions previously duplicated across builtin.py,
read_office.py, grep.py, glob.py, read_image.py, etc.
"""
from __future__ import annotations

import hashlib
from typing import Any


def require_runtime(context: Any) -> Any:
    """Assert that the runtime execution client is injected on ToolContext.

    All tool execution must go through context.runtime (WorkspaceRuntimeClient
    or InProcessRuntimeClient). Raises RuntimeError if not configured.
    """
    rt = getattr(context, "runtime", None)
    if rt is None:
        raise RuntimeError(
            "ToolContext.runtime 未配置；所有工具执行都必须经由统一的 "
            "RuntimeExecutor。不存在本地子进程的回退路径。"
        )
    return rt


def ws_kind(context: Any) -> str:
    """Return template_id for runtime routing, defaulting to __default__."""
    wt = getattr(context, "workspace_tag", None)
    if wt is not None and hasattr(wt, "template_id"):
        return str(wt.template_id)
    return "__default__"


def ws_root(context: Any) -> str:
    """Return workspace root path as string for runtime calls."""
    return str(context.workspace_path)


def sha256hex(data: bytes | str) -> str:
    """SHA-256 hex digest of the given content."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()
