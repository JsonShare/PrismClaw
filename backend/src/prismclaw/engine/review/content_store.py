"""ContentStore — content-addressable blob storage with integrity as a contract.

This replaces the previous "bare blob" approach where bytes were written and
read with no verification. ``get`` always re-checks the SHA-256, so a corrupted
backup can never be silently restored (this is the fix for the old
"read blob, trust it" bug).

The on-disk layout is intentionally compatible with the legacy
``ChangeTrackingStore`` blobs dir (``<edits>/blobs/<sha256hex>``) so existing
backups remain readable during the migration window.

ContentRef and ContentStore Protocol are defined in
``prismclaw.contracts.agent.review``; this module re-exports them for
backward-compatibility and keeps the engine-internal FileSystemContentStore.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from prismclaw.schema.agent.review import ContentRef, ContentStore
from prismclaw.schema.agent.review_errors import IntegrityError

logger = logging.getLogger(__name__)


class FileSystemContentStore:
    """Filesystem-backed ContentStore rooted at ``<edits_dir>/blobs``."""

    def __init__(self, edits_dir: Path) -> None:
        self._edits_dir = edits_dir
        self._blobs_dir = edits_dir / "blobs"

    # -- ContentStore contract ------------------------------------------
    def put(self, data: bytes) -> ContentRef:
        sha = hashlib.sha256(data).hexdigest()
        ref = ContentRef(sha256=sha, ref=f"blobs/{sha}")
        blob_path = self._blobs_dir / sha
        if not blob_path.exists():
            self._blobs_dir.mkdir(parents=True, exist_ok=True)
            # Write-temp then atomic rename so a crash mid-write never leaves a
            # half blob that still hashes correctly (it wouldn't, but the rename
            # also avoids readers seeing a partial file).
            tmp = blob_path.with_suffix(f".tmp.{hashlib.sha256(data[:1]).hexdigest()[:8]}")
            try:
                tmp.write_bytes(data)
                tmp.replace(blob_path)
            except OSError:
                logger.warning("content_store.put.write_failed", exc_info=True)
        return ref

    def get(self, ref: ContentRef) -> bytes:
        blob_path = self._blobs_dir / ref.sha256
        if not blob_path.exists():
            raise IntegrityError(f"blob not found: {ref.ref}")
        data = blob_path.read_bytes()
        actual = hashlib.sha256(data).hexdigest()
        if actual != ref.sha256:
            # Bit-rot / silent corruption — fail loud, never restore garbage.
            raise IntegrityError(
                f"blob integrity mismatch for {ref.ref}: "
                f"expected {ref.sha256}, got {actual}"
            )
        return data

    def exists(self, ref: ContentRef) -> bool:
        return (self._blobs_dir / ref.sha256).exists()

    # -- helpers ---------------------------------------------------------
    @classmethod
    def for_workspace(cls, workspace_path: Path) -> "FileSystemContentStore":
        return cls(workspace_path / ".work" / "edits")
