from __future__ import annotations

import asyncio
import hashlib
from typing import Any

from prismclaw.engine.compression.dedup import (
    dedupe_duplicate_user_messages,
)
from prismclaw.engine.compression.context_zones import clamp_keep_recent
from prismclaw.engine.compression.compaction import (
    estimate_messages_tokens,
    build_compaction,
)
from prismclaw.engine.compression.hardening import (
    filter_oversized_messages,
    has_real_conversation,
    sanity_check_token_estimate,
    strip_tool_result_details,
)
from prismclaw.engine.compression.semantic_fidelity import (
    build_preservation_contract,
    build_semantic_capsule,
    fidelity_metadata,
    validate_semantic_capsule,
)
from prismclaw.engine.state.state_protocol import derive_state_payload
from prismclaw.engine.compression.summary_engine import (
    CompactionAborted,
    CompactionTimeout,
    SummaryEngine,
)
from prismclaw.schema.content.compaction import CompactResult, CompactionArtifact, CompactionBoundary, CompactionPreservedSegment


class HistoryCompactor:
    def __init__(
        self,
        *,
        threshold: float,
        keep_recent_tokens: int,
        summary_engine: SummaryEngine,
    ) -> None:
        self._threshold = threshold
        self._keep_recent_tokens = keep_recent_tokens
        self._summary_engine = summary_engine

    @property
    def threshold(self) -> float:
        return self._threshold

    @property
    def keep_recent_tokens(self) -> int:
        return self._keep_recent_tokens

    async def compact(
        self,
        *,
        session_id: str,
        messages: list[Any],
        token_budget: int,
        force: bool = False,
        abort_event: asyncio.Event | None = None,
        model: str | None = None,
        threshold_override: float | None = None,
        keep_recent_tokens_override: int | None = None,
    ) -> CompactResult:
        if abort_event is not None and abort_event.is_set():
            return CompactResult(
                ok=False,
                compacted=False,
                reason="aborted",
                reason_code="aborted",
            )

        if not has_real_conversation(messages):
            return CompactResult(
                ok=True,
                compacted=False,
                reason="no real conversation",
                reason_code="no_compactable_entries",
            )

        deduped = dedupe_duplicate_user_messages(messages)
        # Keep the cut-time budget check consistent with the pre-trigger
        # assessment (both at history_budget); ``force`` bypasses entirely.
        effective_threshold = 1.0 if force else self._threshold

        _keep_recent = (
            keep_recent_tokens_override
            if keep_recent_tokens_override is not None
            else self._keep_recent_tokens
        )
        _keep_recent = clamp_keep_recent(_keep_recent, token_budget)
        plan = build_compaction(
            deduped,
            context_window=token_budget,
            threshold=effective_threshold,
            keep_recent_tokens=_keep_recent,
        )
        if not plan.should_compact or plan.cut_index is None:
            return CompactResult(
                ok=True,
                compacted=False,
                reason=plan.reason,
                reason_code=(
                    "below_threshold"
                    if plan.reason == "within-budget"
                    else "no_compactable_entries"
                ),
                tokens_before=plan.estimated_tokens,
            )

        to_summarize_raw = deduped[: plan.cut_index]
        to_summarize = strip_tool_result_details(to_summarize_raw)
        to_summarize = filter_oversized_messages(to_summarize, context_window=token_budget)

        try:
            summary = await self._summary_engine.summarize_messages(
                to_summarize,
                abort_event=abort_event,
                model=model,
            )
        except CompactionTimeout:
            return CompactResult(
                ok=False,
                compacted=False,
                reason="summarizer timeout",
                reason_code="timeout",
                tokens_before=plan.estimated_tokens,
            )
        except CompactionAborted:
            return CompactResult(
                ok=False,
                compacted=False,
                reason="aborted",
                reason_code="aborted",
                tokens_before=plan.estimated_tokens,
            )
        except Exception as exc:
            return CompactResult(
                ok=False,
                compacted=False,
                reason=f"summary failed: {exc}",
                reason_code="summary_failed",
                tokens_before=plan.estimated_tokens,
            )

        from prismclaw.engine.compression.compaction import extract_identifiers

        raw_text_for_extraction = ""
        for m in to_summarize:
            content = m.get("content")
            if isinstance(content, str):
                raw_text_for_extraction += content + "\n"
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        raw_text_for_extraction += block.get("text", "") + "\n"

        identifiers = extract_identifiers(raw_text_for_extraction)
        base_summary = summary
        if identifiers:
            summary += "\n\n==== 已保留的技术标识符 ====\n"
            summary += "（请直接使用这些精确路径与哈希继续工作，不要再重复检索）\n"
            for ident in sorted(identifiers):
                summary += f"- {ident}\n"

        tokens_after_raw = plan.kept_tokens + estimate_messages_tokens(
            [{"role": "assistant", "content": summary}]
        )
        tokens_after = sanity_check_token_estimate(plan.estimated_tokens, tokens_after_raw)

        first_kept_id = None
        if plan.cut_index is not None and plan.cut_index < len(deduped):
            first_kept_id = deduped[plan.cut_index].get("__entry_id__")

        state_updates = derive_state_payload(to_summarize_raw) or {}
        preserved_tail_entry_id = _entry_id(deduped[-1]) if deduped else None
        source_refs = [
            entry_id
            for entry_id in (
                _entry_id(to_summarize_raw[0]) if to_summarize_raw else None,
                _entry_id(to_summarize_raw[-1]) if to_summarize_raw else None,
                first_kept_id,
                preserved_tail_entry_id,
            )
            if entry_id is not None
        ]
        preservation_contract = build_preservation_contract(
            scope="history",
            source_id=session_id,
            source_text=raw_text_for_extraction,
            state_updates=state_updates,
            source_refs=source_refs,
            preserved_identifiers=sorted(identifiers),
            loss_budget="low",
        )
        semantic_capsule = build_semantic_capsule(
            capsule_type="history_summary",
            summary_text=base_summary,
            contract=preservation_contract,
            source_text=raw_text_for_extraction,
            source_span_refs=source_refs,
            recovery_handle={
                "session_id": session_id,
                "state_updates": state_updates,
                "source_message_count": len(to_summarize_raw),
            },
            loss_classification="bounded_loss",
            metadata={"preserved_identifier_count": len(identifiers)},
        )
        validation = validate_semantic_capsule(preservation_contract, semantic_capsule)
        return CompactResult(
            ok=True,
            compacted=True,
            summary=summary,
            details=_build_history_compaction_artifact(
                session_id=session_id,
                token_budget=token_budget,
                source_message_count=len(to_summarize_raw),
                compacted_message_count=len(to_summarize),
                summary_text=base_summary,
                preserved_identifiers=sorted(identifiers),
                state_updates=state_updates,
                source_head_entry_id=_entry_id(to_summarize_raw[0]) if to_summarize_raw else None,
                source_tail_entry_id=_entry_id(to_summarize_raw[-1]) if to_summarize_raw else None,
                first_kept_entry_id=first_kept_id,
                preserved_tail_entry_id=preserved_tail_entry_id,
                fidelity=fidelity_metadata(
                    contract=preservation_contract,
                    capsule=semantic_capsule,
                    validation=validation,
                ),
            ),
            first_kept_entry_id=first_kept_id,
            tokens_before=plan.estimated_tokens,
            tokens_after=tokens_after,
            reason=f"compacted-at-{plan.cut_index}",
            reason_code="compacted",
            preservation_contract=preservation_contract,
            semantic_capsule=semantic_capsule,
            validation=validation,
        )


def _build_history_compaction_artifact(
    *,
    session_id: str,
    token_budget: int,
    source_message_count: int,
    compacted_message_count: int,
    summary_text: str,
    preserved_identifiers: list[str],
    state_updates: dict[str, Any],
    source_head_entry_id: str | None,
    source_tail_entry_id: str | None,
    first_kept_entry_id: str | None,
    preserved_tail_entry_id: str | None,
    fidelity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    digest = hashlib.sha1(
        f"{session_id}:{token_budget}:{source_message_count}:{compacted_message_count}:{summary_text[:200]}".encode(
            "utf-8"
        )
    ).hexdigest()
    preserved_segment = None
    if first_kept_entry_id or preserved_tail_entry_id:
        preserved_segment = CompactionPreservedSegment(
            head_entry_id=first_kept_entry_id,
            tail_entry_id=preserved_tail_entry_id,
        )
    artifact = CompactionArtifact.for_history_summary(
        artifact_id=f"hc_{digest[:12]}",
        source_id=session_id,
        summary_text=summary_text,
        source_message_count=source_message_count,
        compacted_message_count=compacted_message_count,
        token_budget=token_budget,
        preserved_identifiers=preserved_identifiers,
        state_updates=state_updates,
        boundary=CompactionBoundary(
            session_id=session_id,
            source_head_entry_id=source_head_entry_id,
            source_tail_entry_id=source_tail_entry_id,
            first_kept_entry_id=first_kept_entry_id,
            preserved_segment=preserved_segment,
        ),
    ).to_metadata_dict()
    if fidelity:
        artifact.update(fidelity)
    return artifact


def _entry_id(message: Any) -> str | None:
    if not isinstance(message, dict):
        return None
    entry_id = message.get("__entry_id__")
    return entry_id if isinstance(entry_id, str) and entry_id else None
