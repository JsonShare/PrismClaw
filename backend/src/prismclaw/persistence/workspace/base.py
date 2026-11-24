from __future__ import annotations

from prismclaw.schema.storage.storage_types import WorkspaceStore as WorkspaceStore

"""Persistence-layer WorkspaceStore re-export.

The canonical Protocol definition lives in contracts.infra.storage_types.WorkspaceStore.
This module re-exports it for backward compatibility with existing import paths.
"""


__all__ = ["WorkspaceStore"]
