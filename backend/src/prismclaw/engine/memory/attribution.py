"""SDD B1: retrieval attribution (docs/memory-dream-sdd.md §5.1).

The feedback loop's foundation — without knowing WHICH memories were
injected into WHICH turns, dream consolidation stays blind and ranking
signals (use_count) stay dead values (defect D4).

Components:
- AttributionRecorder: per-session in-flight injection buffer with
  intra-turn dedup (restart_turn re-assembles the same turn — the same
  entry must not be counted twice), flushed as one batch when the turn
  completes. Also keeps the last completed turn's injections in memory
  for user-correction detection.
- detect_user_correction: explicit-negation rule per SDD §A — negation
  marker + token overlap with an injected memory. Nothing implicit.

Design notes (v3 SDD discipline):
- Signals default NEUTRAL: only clearly decidable events become
  validation events. Weak signals (task-outcome co-occurrence) carry
  attribution='weak'; only 'strong' counts for deep-merge evidence gates.
- All writes are best-effort: attribution must never break retrieval.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from prismclaw.schema.session.ids import derive_memory_session_key

logger = logging.getLogger(__name__)

# Explicit correction markers (SDD §A: explicit negation only — no implicit
# dissatisfaction guessing). A marker alone is NOT a correction; it must also
# overlap with the content of a previously injected memory.
_NEGATION_MARKERS = (
    "不对",
    "不是",
    "错了",
    "有错",
    "搞错",
    "弄错",
    "记错",
    "别再",
    "不要",
    "无需",
    "已经不是",
    "不再用",
    "现在不用",
    "改用",
    "换成",
    "改成",
    "纠正一下",
    "更正",
)

# Token overlap thresholds: shared >= 2 informative tokens, >= 20% of the
# smaller token set, OR any single high-information token (alphanumeric,
# >= 3 chars — proper nouns / tech terms like "postgresql"): a correction
# naming the very entity of the injected memory is strong attribution even
# when it is the ONLY overlap (Chinese function words carry no signal).
_MIN_SHARED_TOKENS = 2
_MIN_OVERLAP_RATIO = 0.2
_INFORMATIVE_MIN_LEN = 3

_STOP_TOKENS = {"的", "了", "是", "在", "我", "你", "他", "她", "它", "们", "和", "与", "或",
                "a", "the", "is", "are", "to", "of", "and", "in", "on", "for", "this", "that"}


def _tokenize(text: str) -> set[str]:
    try:
        import jieba
        tokens = {t.strip().lower() for t in jieba.cut_for_search(text) if t.strip()}
    except Exception:
        tokens = {t.lower() for t in text.split() if t.strip()}
    return tokens - _STOP_TOKENS


def _is_informative_token(token: str) -> bool:
    """High-signal token: alphanumeric, >= 3 chars (proper nouns, tech terms)."""
    return len(token) >= _INFORMATIVE_MIN_LEN and token.isalnum()


def has_explicit_negation(text: str) -> bool:
    return any(marker in text for marker in _NEGATION_MARKERS)


def detect_user_correction(
    user_text: str,
    injected: list[tuple[str, str]],
) -> list[str]:
    """Return entry_ids whose injected content this user message explicitly corrects.

    Rule (SDD §A): explicit negation marker AND token overlap with the
    injected memory. Returns [] when either half is missing — a correction
    we cannot attribute to a specific memory is not recorded per-entry.
    """
    if not user_text or not injected:
        return []
    if not has_explicit_negation(user_text):
        return []
    user_tokens = _tokenize(user_text)
    if len(user_tokens) < 1:
        return []

    hits: list[str] = []
    for entry_id, content in injected:
        if not content:
            continue
        content_tokens = _tokenize(content)
        if not content_tokens:
            continue
        shared = user_tokens & content_tokens
        if not shared:
            continue
        if (
            len(shared) >= _MIN_SHARED_TOKENS
            or any(_is_informative_token(t) for t in shared)
        ):
            hits.append(entry_id)
            continue
        denom = min(len(user_tokens), len(content_tokens))
        if denom and len(shared) / denom >= _MIN_OVERLAP_RATIO:
            hits.append(entry_id)
    return hits


class AttributionRecorder:
    """In-flight injection bookkeeping, flushed once per turn.

    Lifecycle per turn:
      retrieve_evidence → record_injection(...)   (intra-turn dedup by entry_id)
      after_turn        → mark_turn_complete(...) (batch write + use_count bump)

    The last completed turn's injections are kept in memory so the NEXT
    turn's user message can be checked for explicit corrections.
    """

    def __init__(self, memory_store: Any) -> None:
        self._memory_store = memory_store
        # session_id → pending injection records for the in-flight turn
        self._pending: dict[str, list[dict[str, Any]]] = {}
        # session_id → last completed turn's (entry_id, content) pairs
        self._last_turn: dict[str, list[tuple[str, str]]] = {}

    def record_injection(
        self,
        session_id: str,
        *,
        entry_id: str,
        entry_content: str,
        query_text: str | None,
        query_embedding: list[float] | None,
        relevance_score: float | None,
    ) -> None:
        """Buffer one injected memory for the in-flight turn (dedup by entry_id)."""
        pending = self._pending.setdefault(session_id, [])
        if any(rec["entry_id"] == entry_id for rec in pending):
            return  # restart_turn re-assembly must not double-count
        pending.append(
            {
                "id": f"ref_{uuid.uuid4().hex[:16]}",
                "session_id": session_id,
                "entry_id": entry_id,
                "entry_content": entry_content,
                "query_text": query_text,
                "query_embedding": query_embedding,
                "relevance_score": relevance_score,
                "injected_at": time.time(),
            }
        )

    def mark_turn_complete(self, session_id: str, turn_seq: int) -> None:
        """Seal the in-flight turn: fire-and-forget batch write.

        Writes reference-log rows + bumps use_count for every injected
        entry, then remembers this turn's injections for the next turn's
        correction detection. Never raises into the caller.
        """
        pending = self._pending.pop(session_id, None)
        if not pending:
            return
        self._last_turn[session_id] = [
            (rec["entry_id"], rec["entry_content"]) for rec in pending
        ]
        try:
            task = asyncio.get_running_loop().create_task(
                self._flush_turn(session_id, turn_seq, pending)
            )
            # Avoid task garbage-collection warnings; self-log errors inside.
            task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
        except RuntimeError:
            # No running loop (sync test context) — flush inline best-effort.
            try:
                asyncio.get_event_loop().run_until_complete(
                    self._flush_turn(session_id, turn_seq, pending)
                )
            except Exception:
                logger.warning("attribution flush failed for %s", session_id, exc_info=True)

    async def _flush_turn(
        self, session_id: str, turn_seq: int, pending: list[dict[str, Any]]
    ) -> None:
        try:
            session_key = derive_memory_session_key(session_id)
            records = [
                {
                    "id": rec["id"],
                    "session_id": session_id,
                    "turn_seq": turn_seq,
                    "entry_id": rec["entry_id"],
                    "query_text": rec["query_text"],
                    "query_embedding": rec["query_embedding"],
                    "relevance_score": rec["relevance_score"],
                    "injected_at": rec["injected_at"],
                }
                for rec in pending
            ]
            await self._memory_store.record_memory_references(session_key, records)
            await self._memory_store.writeback_use_counts(
                session_key, [rec["entry_id"] for rec in pending]
            )
        except Exception:
            logger.warning(
                "attribution turn flush failed for %s (turn %d)",
                session_id,
                turn_seq,
                exc_info=True,
            )

    def last_turn_injections(self, session_id: str) -> list[tuple[str, str]]:
        return list(self._last_turn.get(session_id, []))

    def pending_entry_ids(self, session_id: str) -> list[str]:
        """Entry ids injected in the current in-flight turn (for task-outcome signals)."""
        return [rec["entry_id"] for rec in self._pending.get(session_id, [])]

    async def record_validation_events(
        self, session_id: str, events: list[dict[str, Any]]
    ) -> int:
        """Persist validation events (correction/task-outcome) for this session."""
        try:
            session_key = derive_memory_session_key(session_id)
            return await self._memory_store.record_validation_events(session_key, events)
        except Exception:
            logger.warning(
                "validation event persist failed for %s", session_id, exc_info=True
            )
            return 0

    @staticmethod
    def build_validation_event(
        *,
        entry_id: str,
        signal_type: str,
        polarity: str,
        attribution: str,
        source_ref: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "id": f"val_{uuid.uuid4().hex[:16]}",
            "entry_id": entry_id,
            "signal_type": signal_type,
            "polarity": polarity,
            "attribution": attribution,
            "source_ref": source_ref,
            "occurred_at": time.time(),
            "payload": payload or {},
        }
