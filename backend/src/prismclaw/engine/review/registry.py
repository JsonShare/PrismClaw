"""ReviewRegistry — the write-side orchestrator for the review subsystem.

Responsibilities:
  * own the ContentStore (integrity-checked blobs)
  * persist every state change as an append-only event (review_events.jsonl)
  * cache live ReviewSets per (workspace, conversation) in memory
  * turn user intents (register edit / reject / confirm) into atomic file
    materializations + recorded decisions

This is the single place that knows how to move a file. The legacy
``ChangeTrackingStore`` text path is deleted; text edits and rejects flow
through here only.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from pathlib import Path

from prismclaw.engine.review.content_store import (
    ContentRef,
    FileSystemContentStore,
)
from prismclaw.schema.agent.review_errors import (
    ReconcileConflict,
    ReviewSetNotFound,
)
from prismclaw.engine.review.events import ReviewDecision, ReviewEvent
from prismclaw.engine.review.materializer import materialize_file
from prismclaw.engine.review.reconcile import ReconcileState, reconcile
from prismclaw.engine.review.review_set import ReviewSet

logger = logging.getLogger(__name__)


def _review_set_id(conversation_id: str) -> str:
    return f"rs:{conversation_id}"


class ReviewRegistry:
    def __init__(self, workspace_path: Path) -> None:
        self._workspace = workspace_path
        self._content = FileSystemContentStore.for_workspace(workspace_path)
        self._events_path = workspace_path / ".work" / "edits" / "review_events.jsonl"
        self._lock = threading.RLock()
        self._cache: dict[str, ReviewSet] = {}  # conversation_id -> live ReviewSet

    # -- public API ------------------------------------------------------
    def get_or_open(self, conversation_id: str) -> ReviewSet:
        with self._lock:
            if conversation_id in self._cache:
                return self._cache[conversation_id]
            rs = self._replay(conversation_id)
            if rs is None:
                rs = ReviewSet(id=_review_set_id(conversation_id), conversation_id=conversation_id)
                self._append(ReviewEvent(review_set_id=rs.id, kind="open"))
            self._cache[conversation_id] = rs
            return rs

    def register_edit(
        self,
        conversation_id: str,
        path: str,
        before_content: bytes | None,
        after_content: bytes,
        session_id: str,
        tool_call_id: str,
        is_binary: bool = False,
    ) -> None:
        """Record one text edit: snapshot baseline (once) + record head."""
        with self._lock:
            rs = self.get_or_open(conversation_id)
            if path not in rs.baseline:
                base_ref = self._content.put(before_content) if before_content is not None else None
                rs.snapshot_baseline(path, base_ref.ref if base_ref is not None else None)
                self._append(ReviewEvent(
                    review_set_id=rs.id, kind="baseline",
                    payload={"path": path, "content_ref": base_ref.ref if base_ref else None},
                ))
            after_ref = self._content.put(after_content)
            rs.record_revision(path, after_ref.ref, after_ref.sha256)
            self._append(ReviewEvent(
                review_set_id=rs.id, kind="revision",
                payload={
                    "path": path,
                    "content_ref": after_ref.ref,
                    "sha256": after_ref.sha256,
                    "session_id": session_id,
                    "tool_call_id": tool_call_id,
                    "is_binary": is_binary,
                },
            ))

    def reject_path(self, conversation_id: str, path: str, force: bool = False) -> dict:
        """Reject one file: return it to its recorded baseline.

        Raises ReconcileConflict if the live file drifted and ``force`` is False.
        A new file (baseline None) is deleted.
        """
        with self._lock:
            rs = self._require(conversation_id)
            expected = rs.head_sha_of(path)
            current = self._current_sha(path)
            state = reconcile(current, expected)
            if state is ReconcileState.DRIFTED and not force:
                raise ReconcileConflict(path, current, expected)

            base_ref = rs.baseline_ref(path)
            if base_ref is None:
                materialize_file(self._workspace, path, None)  # delete new file
                reverted_sha = ""
            else:
                data = self._content.get(ContentRef(sha256=base_ref.split("/")[-1], ref=base_ref))
                materialize_file(self._workspace, path, data)
                reverted_sha = base_ref.split("/")[-1]

            rs.set_decision(path, "rejected")
            self._append(ReviewEvent(
                review_set_id=rs.id, kind="decision",
                payload={"path": path, "decision": "rejected"},
            ))
            self._append(ReviewEvent(
                review_set_id=rs.id, kind="materialized",
                payload={"path": path, "action": "reject", "sha256": reverted_sha},
            ))
            return {"status": "ok", "path": path, "before_sha": reverted_sha}

    def confirm_path(self, conversation_id: str, path: str) -> None:
        with self._lock:
            rs = self._require(conversation_id)
            rs.set_decision(path, "accepted")
            self._append(ReviewEvent(
                review_set_id=rs.id, kind="decision",
                payload={"path": path, "decision": "accepted"},
            ))

    def set_decision(self, conversation_id: str, path: str, decision: ReviewDecision) -> None:
        with self._lock:
            rs = self._require(conversation_id)
            rs.set_decision(path, decision)
            self._append(ReviewEvent(
                review_set_id=rs.id, kind="decision",
                payload={"path": path, "decision": decision},
            ))

    # -- internals -------------------------------------------------------
    def _require(self, conversation_id: str) -> ReviewSet:
        rs = self.get_or_open(conversation_id)
        if not rs.baseline and not rs.head:
            raise ReviewSetNotFound(f"no review set for conversation {conversation_id}")
        return rs

    def _current_sha(self, path: str) -> str | None:
        full = self._workspace / path
        if not full.exists():
            return None
        try:
            return hashlib.sha256(full.read_bytes()).hexdigest()
        except OSError:
            return None

    def _replay(self, conversation_id: str) -> ReviewSet | None:
        rs_id = _review_set_id(conversation_id)
        events: list[ReviewEvent] = []
        if self._events_path.exists():
            try:
                for line in self._events_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = ReviewEvent.from_line(line)
                    except Exception:
                        continue  # a corrupt line must not break replay
                    if ev.review_set_id == rs_id:
                        events.append(ev)
            except OSError:
                return None
        if not events:
            return None
        return ReviewSet.rebuild(rs_id, conversation_id, events)

    def _append(self, event: ReviewEvent) -> None:
        self._events_path.parent.mkdir(parents=True, exist_ok=True)
        # Serialize under the same lock that mutated state, so disk and memory
        # can never diverge in order.
        try:
            with self._events_path.open("a", encoding="utf-8") as f:
                f.write(event.to_line() + "\n")
        except OSError:
            logger.error("review_registry.append_failed", exc_info=True)
            raise


_REGISTRIES: dict[str, ReviewRegistry] = {}
_REG_LOCK = threading.Lock()


def get_review_registry(workspace_path: Path) -> ReviewRegistry:
    key = str(workspace_path)
    with _REG_LOCK:
        reg = _REGISTRIES.get(key)
        if reg is None:
            reg = ReviewRegistry(workspace_path)
            _REGISTRIES[key] = reg
        return reg


def _reset_registries_for_test() -> None:
    """Drop all cached registries (test isolation only)."""
    with _REG_LOCK:
        _REGISTRIES.clear()
