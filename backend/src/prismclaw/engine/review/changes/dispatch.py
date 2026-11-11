"""Single routing point for every file kind.

``dispatch(fmt)`` returns the one ``ReviewableChange`` strategy that owns the
review semantics for ``fmt``. The call site never branches on ``kind`` again.
"""

from __future__ import annotations

from prismclaw.engine.review.changes.docx_change import DocxChange
from prismclaw.engine.review.changes.spreadsheet_change import SpreadsheetChange
from prismclaw.engine.review.changes.text_change import TextChange
from prismclaw.engine.review.registry import ReviewRegistry


def dispatch(fmt: str | None, *, registry: ReviewRegistry | None = None):
    """Return the ``ReviewableChange`` strategy for ``fmt``.

    ``registry`` is required only for the text/code strategy (it owns the
    baseline). Office strategies manage their own materialization.
    """
    if fmt in ("xlsx", "pptx"):
        return SpreadsheetChange(fmt)
    if fmt == "docx":
        return DocxChange()
    if registry is None:
        raise ValueError("a ReviewRegistry is required to review text/code files")
    return TextChange(registry)
