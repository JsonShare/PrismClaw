from __future__ import annotations

import logging
from typing import Any

from prismclaw.schema.agent.session import MessageEntry
from prismclaw.schema.storage.storage_types import MemoryStore
from prismclaw.schema.storage.storage_types import SessionStore

logger = logging.getLogger(__name__)

MIN_USER_MESSAGES_FOR_ARCHIVE = 3
SIMPLE_SUMMARY_HEAD = 500
SIMPLE_SUMMARY_TAIL = 500

# SDD A3 (defect D2, docs/memory-dream-sdd.md §1.1): the archive summary must
# be LLM-generated (chunked map-reduce, zero truncation of the input) — the
# legacy head+tail concatenation dropped everything in the middle of long
# sessions. The head+tail fallback stays as the degraded path when no LLM is
# available or the summary call fails (never worse than before).
_ARCHIVE_SUMMARY_TIMEOUT_S = 120.0
_ARCHIVE_CHUNK_TOKEN_BUDGET = 8_000


def _derive_session_key(session_id: str) -> str:
    # SDD context-governance W10: delegate to the shared derivation so web-chat
    # conversation ids (``web:{user}:{conversation_id}``) resolve to the same
    # user-scoped key used by the retriever and the writer.
    from prismclaw.schema.session.ids import derive_memory_session_key

    return derive_memory_session_key(session_id)


def _count_user_messages(tree: Any) -> int:
    count = 0
    for entry in tree.entries.values():
        if isinstance(entry, MessageEntry) and entry.role == "user":
            count += 1
    return count


def _simple_summary(tree: Any) -> str:
    from prismclaw.schema.shared import extract_text_from_content

    texts: list[str] = []
    for entry in tree.entries.values():
        if not isinstance(entry, MessageEntry):
            continue
        if entry.role not in ("user", "assistant"):
            continue
        text = extract_text_from_content(entry.content)
        if text.strip():
            texts.append(f"{entry.role}: {text}")
    joined = "\n".join(texts)
    if len(joined) <= SIMPLE_SUMMARY_HEAD + SIMPLE_SUMMARY_TAIL + 10:
        return joined
    return joined[:SIMPLE_SUMMARY_HEAD] + "\n...\n" + joined[-SIMPLE_SUMMARY_TAIL:]


def _archive_messages(tree: Any) -> list[dict[str, Any]]:
    """Full-text user/assistant transcript in tree order (zero truncation)."""
    from prismclaw.schema.shared import extract_text_from_content

    messages: list[dict[str, Any]] = []
    for eid in tree.order:
        entry = tree.entries.get(eid)
        if not isinstance(entry, MessageEntry):
            continue
        if entry.role not in ("user", "assistant"):
            continue
        text = extract_text_from_content(entry.content)
        if text.strip():
            messages.append({"role": entry.role, "content": text})
    return messages


async def _llm_archive_summary(tree: Any, llm_client: Any) -> str | None:
    """LLM archive summary via the compaction SummaryEngine (chunked map-reduce).

    Returns None on any failure — the caller falls back to _simple_summary.
    """
    try:
        from prismclaw.schema.llm.request import PackedLLMRequest
        from prismclaw.engine.compression.summary_engine import SummaryEngine

        messages = _archive_messages(tree)
        if not messages:
            return None

        async def _summarize(payload: list[dict[str, Any]], *, model: str | None = None) -> str:
            response = await llm_client.complete(
                request=PackedLLMRequest(
                    messages=payload,
                    system="",
                    context_payload=None,
                    cache_strategy={},
                ),
                model=model,
            )
            return response.text

        engine = SummaryEngine(
            summarize=_summarize,
            compaction_timeout_s=_ARCHIVE_SUMMARY_TIMEOUT_S,
            chunk_token_budget=_ARCHIVE_CHUNK_TOKEN_BUDGET,
        )
        summary = await engine.summarize_messages(messages, abort_event=None, model=None)
        text = (summary or "").strip()
        return text or None
    except Exception:
        logger.warning(
            "LLM archive summary failed; falling back to simple summary",
            exc_info=True,
        )
        return None


async def archive_session_background(
    memory_store: MemoryStore,
    session_store: SessionStore,
    old_session_id: str,
    *,
    llm_client: Any = None,
) -> None:
    try:
        tree = await session_store.load(old_session_id)
        if tree is None:
            logger.info("archive skipped: session %s not found", old_session_id)
            return

        user_msgs = _count_user_messages(tree)
        if user_msgs < MIN_USER_MESSAGES_FOR_ARCHIVE:
            logger.info(
                "archive skipped: session %s has only %d user messages (< %d)",
                old_session_id,
                user_msgs,
                MIN_USER_MESSAGES_FOR_ARCHIVE,
            )
            return

        summary: str | None = None
        summary_engine = "simple"
        if llm_client is not None:
            summary = await _llm_archive_summary(tree, llm_client)
            if summary is not None:
                summary_engine = "llm"
        if not summary:
            summary = _simple_summary(tree)
        if not summary:
            logger.info("archive skipped: session %s has no summarizable content", old_session_id)
            return

        session_key = _derive_session_key(old_session_id)
        # SDD B5: normalized payload — provenance for offline replay.
        total_messages = sum(
            1
            for entry in tree.entries.values()
            if isinstance(entry, MessageEntry) and entry.role in ("user", "assistant")
        )
        await memory_store.archive_session(
            session_key,
            old_session_id,
            summary,
            payload={
                "user_messages": user_msgs,
                "total_messages": total_messages,
                "summary_engine": summary_engine,
            },
        )
        logger.info("archived session %s to L4", old_session_id)
    except Exception:
        logger.warning("archive_session_background failed for %s", old_session_id, exc_info=True)
