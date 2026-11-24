from __future__ import annotations

from prismclaw.schema.storage.storage_types import MemoryStore as MemoryStore

"""Persistence-layer MemoryStore re-export.

The canonical Protocol definition lives in contracts.infra.storage_types.MemoryStore.
This module re-exports it for backward compatibility with existing import paths.
"""


__all__ = ["MemoryStore"]
