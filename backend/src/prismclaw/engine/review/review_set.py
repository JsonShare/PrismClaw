"""ReviewSet — the aggregate root (the missing first-class concept).

A ReviewSet is one "agent edited → user reviews" unit, bound to a conversation.
Its defining feature is ``baseline``: the snapshot of every touched file's
pre-edit state, taken once when the file is first seen. "Reject" is then
unambiguously "return this file to its baseline" — never "undo the last edit".

ReviewSet is a *pure state container*. It performs no IO. The registry is
responsible for persisting state changes as events and for materializing files.
This separation is what makes the model testable and the write-path atomic.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from prismclaw.engine.review.events import ReviewDecision, ReviewEvent


@dataclass
class ReviewSet:
    id: str
    conversation_id: str
    baseline: dict[str, str | None] = field(default_factory=dict)  # path -> content_ref | None(new)
    head: dict[str, str] = field(default_factory=dict)            # path -> content_ref
    head_sha: dict[str, str] = field(default_factory=dict)        # path -> sha256 of head content
    decision: dict[str, ReviewDecision] = field(default_factory=dict)  # path -> decision
    status: str = "open"  # "open" | "sealed"

    # -- pure state mutators (no IO) ------------------------------------
    def snapshot_baseline(self, path: str, content_ref: str | None) -> None:
        if path in self.baseline:
            return  # baseline captured exactly once, at first touch
        self.baseline[path] = content_ref

    def record_revision(self, path: str, content_ref: str, sha256: str) -> None:
        self.head[path] = content_ref
        self.head_sha[path] = sha256
        self.decision.setdefault(path, "proposed")

    def set_decision(self, path: str, decision: ReviewDecision) -> None:
        self.decision[path] = decision

    def baseline_ref(self, path: str) -> str | None:
        """Content ref to restore on reject. None means 'was a new file'."""
        return self.baseline.get(path)

    def head_sha_of(self, path: str) -> str | None:
        return self.head_sha.get(path)

    # -- event sourcing --------------------------------------------------
    @classmethod
    def rebuild(cls, review_set_id: str, conversation_id: str, events: list[ReviewEvent]) -> "ReviewSet":
        rs = cls(id=review_set_id, conversation_id=conversation_id)
        for ev in events:
            p = ev.payload
            if ev.kind == "baseline":
                rs.baseline[p["path"]] = p.get("content_ref")  # None => new file
            elif ev.kind == "revision":
                rs.head[p["path"]] = p["content_ref"]
                rs.head_sha[p["path"]] = p["sha256"]
                rs.decision.setdefault(p["path"], "proposed")
            elif ev.kind == "decision":
                rs.decision[p["path"]] = p["decision"]
        return rs
