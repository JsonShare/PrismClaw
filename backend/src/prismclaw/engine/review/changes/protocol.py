"""The unified review contract.

Every file kind (text, xlsx, pptx, docx) implements ``ReviewableChange``. The
call site does ``dispatch(fmt).reject(intent)`` and never switches on kind.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from prismclaw.engine.review.changes.intent import ReviewIntent


@runtime_checkable
class ReviewableChange(Protocol):
    kind: str

    def reject(self, intent: ReviewIntent) -> dict[str, Any]:
        """Apply a Reject decision and return a response dict."""
        ...

    def accept(self, intent: ReviewIntent) -> dict[str, Any]:
        """Apply an Accept decision and return a response dict."""
        ...
