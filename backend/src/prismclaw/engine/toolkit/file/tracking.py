"""Change-tracking store — append-only edit log + content-addressable blob backup.

Provides the foundation for file-edit review-and-revert:
  - Every EditTool / WriteTool call saves the *before* content to a blob (SHA-256 keyed, deduped).
  - A FileEditRecord is appended to log.jsonl for each edit.
  - The Revert HTTP API reads the blob and restores the file, then records a new revert entry.

Storage layout (under workspace_path/.work/edits/):
  log.jsonl          — one FileEditRecord per line (append-only)
  blobs/<sha256hex>  — content-addressed backup files (deduped by hash)
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

from prismclaw.engine.toolkit.builtin.shared_tool_helpers import sha256hex

logger = logging.getLogger(__name__)


@dataclass
class FileEditRecord:
    """One edit event — mirrors what the frontend needs for review + revert."""

    record_id: str
    session_id: str
    tool_call_id: str
    path: str           # relative path within workspace
    before_sha: str      # SHA-256 of before-content (hex)
    after_sha: str       # SHA-256 of after-content  (hex)
    before_ref: str      # blob path for before-content
    after_ref: str       # blob path for after-content  (may be empty for WriteTool if not persisted yet)
    ts: float            # unix epoch seconds
    binary: bool = False # True for non-text blobs (e.g. .docx bytes); restore_file branches on this


@dataclass
class DocxReviewArtifactRecord:
    """One generated `.docx` review artifact bound to a source session/path/blob."""

    artifact_id: str
    session_id: str
    path: str
    before_ref: str
    review_docx_ref: str
    review_docx_sha: str
    change_count: int
    ts: float


_sha256hex = sha256hex
_sha256_bytes = sha256hex


class ChangeTrackingStore:
    """Append-only edit log with content-addressable blob storage."""

    def __init__(self, edits_dir: Path) -> None:
        self._edits_dir = edits_dir
        self._blobs_dir = edits_dir / "blobs"
        self._log_path = edits_dir / "log.jsonl"
        self._docx_review_log_path = edits_dir / "docx_review_artifacts.jsonl"

    def _ensure_dirs(self) -> None:
        self._edits_dir.mkdir(parents=True, exist_ok=True)
        self._blobs_dir.mkdir(parents=True, exist_ok=True)

    def save_blob(self, content: str) -> str:
        """Save *text* content to a blob keyed by SHA-256. Returns the relative path under blobs/.

        Deduped — same content always maps to the same blob file, no duplicate writes.
        """
        self._ensure_dirs()
        sha = _sha256hex(content)
        blob_path = self._blobs_dir / sha
        if not blob_path.exists():
            try:
                blob_path.write_text(content, encoding="utf-8")
            except OSError:
                logger.warning("tracking.save_blob.write_failed", exc_info=True)
        return f"blobs/{sha}"

    def save_blob_bytes(self, content: bytes) -> str:
        """Save *binary* content (e.g. .docx bytes) to a blob keyed by SHA-256.

        Mirrors save_blob but operates on raw bytes so non-text files round-trip intact.
        """
        self._ensure_dirs()
        sha = _sha256_bytes(content)
        blob_path = self._blobs_dir / sha
        if not blob_path.exists():
            try:
                blob_path.write_bytes(content)
            except OSError:
                logger.warning("tracking.save_blob_bytes.write_failed", exc_info=True)
        return f"blobs/{sha}"

    def read_blob(self, ref: str) -> str | None:
        """Read *text* content from a blob by its relative path. Returns None if not found."""
        blob_path = self._edits_dir / ref
        if not blob_path.exists():
            return None
        try:
            return blob_path.read_text(encoding="utf-8")
        except OSError:
            logger.warning("tracking.read_blob.read_failed", exc_info=True)
            return None

    def read_blob_bytes(self, ref: str) -> bytes | None:
        """Read *binary* content from a blob by its relative path. Returns None if not found."""
        blob_path = self._edits_dir / ref
        if not blob_path.exists():
            return None
        try:
            return blob_path.read_bytes()
        except OSError:
            logger.warning("tracking.read_blob_bytes.read_failed", exc_info=True)
            return None

    def record(self, rec: FileEditRecord) -> None:
        """Append a FileEditRecord to log.jsonl."""
        self._ensure_dirs()
        line = json.dumps(asdict(rec), ensure_ascii=False) + "\n"
        try:
            with self._log_path.open("a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            logger.warning("tracking.record.write_failed", exc_info=True)

    def record_docx_review_artifact(self, rec: DocxReviewArtifactRecord) -> None:
        """Append a generated `.docx` review artifact record."""
        self._ensure_dirs()
        line = json.dumps(asdict(rec), ensure_ascii=False) + "\n"
        try:
            with self._docx_review_log_path.open("a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            logger.warning("tracking.record_docx_review_artifact.write_failed", exc_info=True)

    def get_docx_review_artifact(
        self,
        *,
        session_id: str,
        review_docx_ref: str,
    ) -> DocxReviewArtifactRecord | None:
        """Find a generated `.docx` review artifact by ref within one session."""
        if not self._docx_review_log_path.exists():
            return None
        try:
            lines = self._docx_review_log_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return None
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                data.get("session_id") == session_id
                and data.get("review_docx_ref") == review_docx_ref
            ):
                return DocxReviewArtifactRecord(**data)
        return None

def get_tracking_store(workspace_path: Path) -> ChangeTrackingStore:
    """Factory — returns a ChangeTrackingStore rooted at workspace_path/.work/edits/."""
    edits_dir = workspace_path / ".work" / "edits"
    return ChangeTrackingStore(edits_dir)
