"""Text/code change — reject restores the whole file to its ReviewSet baseline."""

from __future__ import annotations

from typing import Any

from prismclaw.engine.review.changes.intent import ReviewIntent
from prismclaw.engine.review.registry import ReviewRegistry


class TextChange:
    kind = "text"

    def __init__(self, registry: ReviewRegistry) -> None:
        self._registry = registry

    def reject(self, intent: ReviewIntent) -> dict[str, Any]:
        return self._registry.reject_path(
            intent.conversation_id, intent.path, force=intent.force
        )

    def accept(self, intent: ReviewIntent) -> dict[str, Any]:
        self._registry.confirm_path(intent.conversation_id, intent.path)
        return {"status": "ok", "path": intent.path}
