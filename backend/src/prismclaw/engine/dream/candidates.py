from __future__ import annotations

from hashlib import sha256
from typing import Any

from prismclaw.schema.agent.session import MessageEntry
from prismclaw.schema.shared.utils import extract_text_from_content
from prismclaw.schema.dream.dream import DreamCandidateStore
from prismclaw.schema.dream.dream_types import DreamCandidateKind, DreamMemoryCandidate
from prismclaw.schema.storage.storage_types import SessionStore

_SESSION_SUMMARY_HEAD = 1_000
_SESSION_SUMMARY_TAIL = 1_000


class DreamCandidateCollector:
    def __init__(
        self,
        *,
        session_store: SessionStore,
        candidate_store: DreamCandidateStore,
    ) -> None:
        self._session_store = session_store
        self._candidate_store = candidate_store

    async def capture_turn(self, session_id: str, session_key: str) -> bool:
        tree = await self._session_store.load(session_id)
        if tree is None or tree.leaf_id is None:
            return False
        entries = _recent_message_entries(tree.get_branch(), limit=4)
        if not entries:
            return False
        transcript = _render_entries(entries)
        if not transcript:
            return False
        source_ids = [entry.id for entry in entries]
        leaf_id = str(tree.leaf_id)
        await self._candidate_store.upsert(
            DreamMemoryCandidate(
                candidate_id=_candidate_id("turn_context", session_id, leaf_id),
                session_key=session_key,
                session_id=session_id,
                kind="turn_context",
                content=transcript,
                source_entry_ids=source_ids,
                metadata={
                    "leaf_id": leaf_id,
                    "message_count": len(entries),
                    "capture_mode": "after_turn",
                },
            )
        )
        return True

    async def capture_session(self, session_id: str, session_key: str) -> bool:
        tree = await self._session_store.load(session_id)
        if tree is None:
            return False
        summary = _build_session_episode_summary(tree.get_branch())
        if not summary:
            return False
        source_ids = [
            entry.id
            for entry in tree.get_branch()
            if isinstance(entry, MessageEntry) and not entry.partial
        ]
        source_count = len(source_ids)
        anchor_id = str(tree.leaf_id or session_id)
        await self._candidate_store.upsert(
            DreamMemoryCandidate(
                candidate_id=_candidate_id("session_episode", session_id, anchor_id),
                session_key=session_key,
                session_id=session_id,
                kind="session_episode",
                content=summary,
                source_entry_ids=source_ids,
                metadata={
                    "leaf_id": anchor_id,
                    "message_count": source_count,
                    "capture_mode": "consolidate_session",
                },
            )
        )
        return True


def _candidate_id(kind: DreamCandidateKind, session_id: str, anchor: str) -> str:
    digest = sha256(f"{kind}:{session_id}:{anchor}".encode("utf-8")).hexdigest()[:16]
    return f"{kind}:{digest}"


def _recent_message_entries(entries: list[Any], *, limit: int) -> list[MessageEntry]:
    selected: list[MessageEntry] = []
    for entry in reversed(entries):
        if not isinstance(entry, MessageEntry):
            continue
        if entry.partial:
            continue
        text = extract_text_from_content(entry.content).strip()
        if not text:
            continue
        if entry.role not in {"user", "assistant", "tool"}:
            continue
        selected.append(entry)
        if len(selected) >= limit:
            break
    selected.reverse()
    return selected


def _render_entries(entries: list[MessageEntry]) -> str:
    lines: list[str] = []
    for entry in entries:
        text = extract_text_from_content(entry.content).strip()
        if not text:
            continue
        lines.append(f"{entry.role}: {text}")
    return "\n".join(lines).strip()


def _build_session_episode_summary(entries: list[Any]) -> str:
    lines: list[str] = []
    for entry in entries:
        if not isinstance(entry, MessageEntry):
            continue
        if entry.partial or entry.role not in {"user", "assistant"}:
            continue
        text = extract_text_from_content(entry.content).strip()
        if not text:
            continue
        lines.append(f"{entry.role}: {text}")
    if not lines:
        return ""
    joined = "\n".join(lines)
    if len(joined) <= _SESSION_SUMMARY_HEAD + _SESSION_SUMMARY_TAIL + 10:
        return joined
    return joined[:_SESSION_SUMMARY_HEAD] + "\n...\n" + joined[-_SESSION_SUMMARY_TAIL:]
