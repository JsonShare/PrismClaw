from __future__ import annotations

"""Persistence-layer WorkspaceStore re-export.

The canonical Protocol definition lives in contracts.infra.storage_types.WorkspaceStore.
This module re-exports it for backward compatibility with existing import paths.
"""

from prismclaw.schema.storage.storage_types import WorkspaceStore as WorkspaceStore

__all__ = ["WorkspaceStore"]
