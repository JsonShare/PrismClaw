"""Review subsystem domain errors.

Moved from engine.agent.review.errors, engine.agent.review.changes,
and engine.agent.tools.office.docx_review_native to break
delivery → engine dependency (G2).
"""
from __future__ import annotations


# --- Chain 1: General review errors (from engine.agent.review.errors) ---

class ReviewError(Exception):
    """Base class for all review-subsystem errors."""


class IntegrityError(ReviewError):
    """A content blob failed its SHA-256 integrity check on read."""


class ReconcileConflict(ReviewError):
    """The live file no longer matches the ReviewSet head when we tried to act."""

    def __init__(self, path: str, current_sha: str | None, expected_sha: str | None) -> None:
        self.path = path
        self.current_sha = current_sha
        self.expected_sha = expected_sha
        super().__init__(
            f"file drifted before review: {path} "
            f"(current={current_sha}, expected={expected_sha})"
        )


class OfficeEditError(ReviewError):
    """Raised when a structured office operation cannot be applied."""


class OfficeRevertConflict(OfficeEditError):
    """An office change-ledger no longer matches the live file on apply/revert."""


class OfficeBlobNotFound(OfficeEditError):
    """A referenced office backup blob is missing from the tracking store."""


class ReviewSetNotFound(ReviewError):
    """No ReviewSet exists for the requested scope."""


# --- Chain 1 extensions (from engine.agent.review.changes.docx_change) ---

class DocxArtifactMismatch(OfficeEditError):
    """The stored review artifact no longer matches the current review session."""


class DocxSourceDrift(OfficeEditError):
    """The source .docx changed after the review artifact was generated."""


# --- Chain 2: DOCX native review bridge errors (from engine.agent.tools.office.docx_review_native) ---

class DocxNativeReviewError(Exception):
    """Base class for native DOCX review failures."""

    code = "DOCX_NATIVE_REVIEW_ERROR"


class UnsupportedDocxChange(DocxNativeReviewError):
    """Raised when a requested change cannot be expressed as tracked changes."""

    code = "UNSUPPORTED_CHANGE"


class UnsupportedDocxGeometry(DocxNativeReviewError):
    """Raised when the source document shape cannot be mapped safely."""

    code = "UNSUPPORTED_DOC_GEOMETRY"


class DocxReviewEngineGap(DocxNativeReviewError):
    """Raised when the node bridge or review engine cannot complete the build."""

    code = "REVIEW_ENGINE_GAP"
