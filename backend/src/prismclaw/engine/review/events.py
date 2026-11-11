"""Event sourcing for ReviewSet.

A ReviewSet is rebuilt by replaying its event stream. Every state change is an
append-only event; there is no in-place mutation of persisted state.

Event kinds:
  open       — a ReviewSet was created for a conversation
  baseline    — a path's pre-edit baseline was snapshotted (ref None == new file)
  revision    — a new head version was recorded for a path
  decision    — a user decision (proposed|accepted|rejected) for a path
  materialized— a baseline was physically written back to disk (audit)
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

ReviewDecision = Literal["proposed", "accepted", "rejected"]


@dataclass
class ReviewEvent:
    review_set_id: str
    kind: Literal["open", "baseline", "revision", "decision", "materialized"]
    ts: float = field(default_factory=time.time)
    payload: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def to_line(self) -> str:
        return _dumps(asdict(self))

    @classmethod
    def from_line(cls, line: str) -> "ReviewEvent":
        d = _loads(line)
        return cls(
            review_set_id=d["review_set_id"],
            kind=d["kind"],
            ts=d.get("ts", 0.0),
            payload=d.get("payload", {}),
            event_id=d.get("event_id", uuid.uuid4().hex[:12]),
        )


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


def _loads(line: str) -> Any:
    return json.loads(line)
