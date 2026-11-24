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


class ReconcileConflictError(ReviewError):
    """The live file no longer matches the ReviewSet head when we tried to act."""

    def __init__(self, path: str, current_sha: str | None, expected_sha: str | None) -> None:
        self.path = path
        self.current_sha = current_sha
        self.expected_sha = expected_sha
        super().__init__(
            f"file drifted before review: {path} (current={current_sha}, expected={expected_sha})"
        )


class OfficeEditError(ReviewError):
    """Raised when a structured office operation cannot be applied."""


class OfficeRevertConflictError(OfficeEditError):
    """An office change-ledger no longer matches the live file on apply/revert."""


class OfficeBlobNotFoundError(OfficeEditError):
    """A referenced office backup blob is missing from the tracking store."""


class ReviewSetNotFoundError(ReviewError):
    """No ReviewSet exists for the requested scope."""


# --- Chain 1 extensions (from engine.agent.review.changes.docx_change) ---


class DocxArtifactMismatchError(OfficeEditError):
    """The stored review artifact no longer matches the current review session."""


class DocxSourceDriftError(OfficeEditError):
    """The source .docx changed after the review artifact was generated."""


# --- DOCX native 审阅桥错误 ---


class DocxNativeReviewError(Exception):
    """Base class for native DOCX review failures."""

    code = "DOCX_NATIVE_REVIEW_ERROR"


class UnsupportedDocxChangeError(DocxNativeReviewError):
    """Raised when a requested change cannot be expressed as tracked changes."""

    code = "UNSUPPORTED_CHANGE"


class UnsupportedDocxGeometryError(DocxNativeReviewError):
    """Raised when the source document shape cannot be mapped safely."""

    code = "UNSUPPORTED_DOC_GEOMETRY"


class DocxReviewEngineGapError(DocxNativeReviewError):
    """Raised when the node bridge or review engine cannot complete the build."""

    code = "REVIEW_ENGINE_GAP"
