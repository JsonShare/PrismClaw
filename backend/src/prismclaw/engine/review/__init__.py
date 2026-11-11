"""File-review subsystem (post-refactor).

Text edits and rejects flow through :class:`ReviewRegistry`. Office files
(xlsx / pptx / docx) implement the same :class:`ReviewableChange` contract via
``changes/dispatch`` — a single routing point with no ``if fmt ==`` branching.
"""

from prismclaw.engine.review.changes.dispatch import dispatch
from prismclaw.engine.review.changes.docx_change import DocxChange
from prismclaw.engine.review.changes.intent import ReviewIntent
from prismclaw.engine.review.changes.spreadsheet_change import SpreadsheetChange
from prismclaw.engine.review.changes.text_change import TextChange
from prismclaw.engine.review.content_store import (
    ContentRef,
    ContentStore,
    FileSystemContentStore,
)
from prismclaw.schema.agent.review_errors import (
    IntegrityError,
    OfficeBlobNotFound,
    OfficeEditError,
    OfficeRevertConflict,
    ReconcileConflict,
    ReviewError,
    ReviewSetNotFound,
)
from prismclaw.engine.review.events import ReviewDecision, ReviewEvent
from prismclaw.engine.review.lifecycle import (
    GcReport,
    collect_referenced_blobs,
    gc_blobs,
)
from prismclaw.engine.review.reconcile import ReconcileState, reconcile
from prismclaw.engine.review.registry import (
    ReviewRegistry,
    get_review_registry,
)
from prismclaw.engine.review.review_set import ReviewSet

__all__ = [
    "ContentRef",
    "ContentStore",
    "FileSystemContentStore",
    "IntegrityError",
    "OfficeEditError",
    "OfficeRevertConflict",
    "OfficeBlobNotFound",
    "ReconcileConflict",
    "ReviewError",
    "ReviewSetNotFound",
    "ReviewDecision",
    "ReviewEvent",
    "GcReport",
    "collect_referenced_blobs",
    "gc_blobs",
    "ReconcileState",
    "reconcile",
    "ReviewRegistry",
    "get_review_registry",
    "ReviewSet",
    "ReviewIntent",
    "dispatch",
    "TextChange",
    "SpreadsheetChange",
    "DocxChange",
]
