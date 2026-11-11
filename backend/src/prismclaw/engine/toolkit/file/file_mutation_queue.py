"""Per-file serial mutation queue for concurrent write safety.

Provides in-process serialization of write/edit operations targeting the
same file. Cross-process safety relies on atomic write (write-to-temp +
rename) combined with read_epoch optimistic concurrency checks.
"""
from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from typing import Any


_mutation_queues: dict[str, asyncio.Lock] = {}


async def with_file_mutation_queue(
    file_path: str,
    fn: Callable[[], Awaitable[Any]],
) -> Any:
    """Execute fn while holding a per-file lock.

    Uses workspace-relative path normalization (not os.path.realpath) to
    avoid symlink inconsistencies in sandboxed environments.
    """
    canonical = _workspace_relative_path(file_path)
    lock = _mutation_queues.setdefault(canonical, asyncio.Lock())
    async with lock:
        return await fn()


def clear_mutation_queues() -> None:
    """Clear all mutation locks. Called at session end."""
    _mutation_queues.clear()


def _workspace_relative_path(file_path: str) -> str:
    """Normalize path relative to workspace root.

    Uses os.path.normpath (not realpath) to preserve symlinks as-is.
    """
    return os.path.normpath(file_path)
