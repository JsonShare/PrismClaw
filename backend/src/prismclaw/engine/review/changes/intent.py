"""Carries everything a review strategy needs to apply a user decision.

One object, passed to ``ReviewableChange.reject/accept`` — so the call site
never branches on file kind again.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from prismclaw.engine.review.registry import ReviewRegistry


@dataclass
class ReviewIntent:
    workspace_path: Path
    path: str
    conversation_id: str | None = None
    current_bytes: bytes | None = None  # live file bytes (office in-place)
    rejected: list[dict[str, Any]] = field(default_factory=list)  # office subset to reverse
    accepted: list[dict[str, Any]] = field(default_factory=list)
    force: bool = False
    # docx-native review flow
    before_ref: str | None = None
    before_sha: str | None = None
    review_docx_ref: str | None = None
    operations: list[dict[str, Any]] = field(default_factory=list)
    author: str | None = None
    reviewed_base64: str | None = None
    registry: ReviewRegistry | None = None
