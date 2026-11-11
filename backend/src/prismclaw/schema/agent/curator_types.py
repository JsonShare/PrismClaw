"""Curator type definitions — pure data, no behaviour.

Lives in contracts (L0) so that Protocol definitions can reference these
types without depending on engine (L2).  See SDD §5.1.5.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CuratorReport:
    total_scanned: int = 0
    total_archived: int = 0
    total_graduated: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class CycleReport:
    acquired: bool
    scan_report: CuratorReport | None = None
    review_action_count: int = 0
    error: Any = None
    unexpected_exception: bool = False


@dataclass
class ReviewDecision:
    id: str
    decision: str
    reason: str


@dataclass(frozen=True)
class ReviewOutcome:
    """Per-session result of :func:`run_llm_review`.

    Replaces the legacy ``int`` action_count return to preserve information
    for observability (promoted vs archived vs failed) and for the future
    ``persist-curator-review-metadata`` change (per proposal D3).
    """

    session_key: str
    entries_reviewed: int
    promoted_count: int
    archived_count: int
    failed_count: int

    @property
    def total_actions(self) -> int:
        return self.promoted_count + self.archived_count


@dataclass
class SopRow:
    entry_id: str
    session_key: str
    content: str
    use_count: int
    last_used_at: float | None


@dataclass
class ArchivedSopRow:
    entry_id: str
    session_key: str
    content: str
    archived_at: float | None
