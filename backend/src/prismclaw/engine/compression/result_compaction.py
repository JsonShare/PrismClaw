from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from prismclaw.engine.foundation.tool_output import truncate_tool_result
from prismclaw.schema.agent.data_types import TextBlock
from prismclaw.schema.content.compaction import CompactionArtifact
from prismclaw.schema.content.content_types import ToolResult
from prismclaw.schema.content.context import ContextBlock
from prismclaw.schema.content.replacement_state import ReplacementDecisionState
from prismclaw.schema.content.replacement_store import replacement_decision_from_artifact
from prismclaw.schema.content.compaction import parse_compaction_artifact

# ── Re-exported from sub-modules ──────────────────────────────────────────────
from prismclaw.engine.compression._helpers import (  # noqa: F401
    _content_digest,
    _context_block_reference_id,
    _preview_text,
    _reference_id,
    _result_text,
    _result_text_len,
)
from prismclaw.engine.compression._material_policy import (  # noqa: F401
    ResultCompactionPolicy,
    ResultMaterialType,
    _context_block_policy_for,
    _detect_context_block_material_type,
    _detect_material_type,
    _looks_like_structured_json,
    _looks_like_table,
    _policy_for,
)
from prismclaw.engine.compression._semantic_compress import (  # noqa: F401
    _compact_document_extraction_text,
    _compact_retrieval_list_text,
    _compact_structured_dataset_text,
    _compact_web_page_text,
    _semantic_compact_text,
)
from prismclaw.engine.compression._result_persist import (  # noqa: F401
    _PERSISTED_PATH_RE,
    _materialize_single_result,
    _persist_precompression_original,
    _persist_reference,
    _tool_allows_persist,
    _tool_obj_for_call,
)
from prismclaw.engine.compression._result_metadata import (  # noqa: F401
    _attach_compaction_metadata,
    _attach_context_block_compaction_metadata,
    _extract_block_state_updates,
    _extract_result_state_updates,
    _loss_budget_for_strategies,
    _loss_classification_for_strategies,
    _replace_context_block_with_reference_preview,
    _replace_with_reference_preview,
    _result_replacement_state,
)
from prismclaw.engine.compression._result_budget import (  # noqa: F401
    _coerce_positive_int,
    _coerce_ratio,
    _resolve_aggregate_budget_chars,
    _resolve_context_block_aggregate_budget_chars,
    _resolve_default_max_chars,
    _resolve_model_for_budget,
)
from prismclaw.engine.compression._result_llm_summary import (  # noqa: F401
    _load_per_result_prompt,
    _per_result_should_skip,
    _resolve_per_result_summary,
    run_per_result_fork_agent,
)

logger = logging.getLogger(__name__)


def _tool_result_compaction_payload(result: ToolResult) -> dict[str, Any] | None:
    ui_artifact = result.ui_artifact
    if ui_artifact is None or ui_artifact.artifact_type != "result_compaction_preview":
        return None
    preview = ui_artifact.result_compaction_preview
    return preview if isinstance(preview, dict) else None


@dataclass
class ResultCompactionArtifact:
    tool_call: dict[str, Any]
    result: ToolResult
    material_type: ResultMaterialType
    aggregate_priority: int
    original_chars: int
    compacted_chars: int
    strategies: list[str]
    state_source_text: str


@dataclass
class ResultCompactionTurn:
    artifacts: list[ResultCompactionArtifact]
    aggregate_budget_chars: int
    total_original_chars: int
    total_compacted_chars: int
    replacement_state: ReplacementDecisionState


@dataclass
class ContextBlockCompactionArtifact:
    block: ContextBlock
    material_type: ResultMaterialType
    aggregate_priority: int
    original_chars: int
    compacted_chars: int
    strategies: list[str]


def compact_tool_results_for_turn(
    *,
    tool_calls: list[dict[str, Any]],
    results: list[ToolResult],
    deps: Any,
    frozen_replacement_state: dict[str, str] | None = None,
    working_dir: str | Path | None = None,
) -> ResultCompactionTurn:
    """Sync turn compactor.  When ``per_result_summary.enabled`` is on
    AND ``deps.summarize_fn`` is wired, prefer the async
    ``compact_tool_results_for_turn_async`` to honor the LLM pass.
    This sync path always falls back to character truncation."""
    if _resolve_per_result_summary(deps) is not None and getattr(
        deps, "summarize_fn", None
    ) is not None:
        logger.warning(
            "compact_tool_results_for_turn called on the sync path with "
            "per_result_summary enabled; falling back to character "
            "truncation.  Call compact_tool_results_for_turn_async to "
            "exercise the LLM path."
        )
    return _compact_tool_results_for_turn_sync(
        tool_calls=tool_calls,
        results=results,
        deps=deps,
        frozen_replacement_state=frozen_replacement_state,
        working_dir=working_dir,
    )


async def compact_tool_results_for_turn_async(
    *,
    tool_calls: list[dict[str, Any]],
    results: list[ToolResult],
    deps: Any,
    frozen_replacement_state: dict[str, str] | None = None,
    working_dir: str | Path | None = None,
) -> ResultCompactionTurn:
    """Async turn compactor.  Mirrors
    ``compact_tool_results_for_turn`` but uses
    ``_compact_single_result_async`` so the per-result LLM pass can
    run.
    """
    prs = _resolve_per_result_summary(deps)
    if prs is None or getattr(deps, "summarize_fn", None) is None:
        return _compact_tool_results_for_turn_sync(
            tool_calls=tool_calls,
            results=results,
            deps=deps,
            frozen_replacement_state=frozen_replacement_state,
            working_dir=working_dir,
        )
    return await _compact_tool_results_for_turn_async_impl(
        tool_calls=tool_calls,
        results=results,
        deps=deps,
        frozen_replacement_state=frozen_replacement_state,
        working_dir=working_dir,
    )


def _compact_tool_results_for_turn_sync(
    *,
    tool_calls: list[dict[str, Any]],
    results: list[ToolResult],
    deps: Any,
    frozen_replacement_state: dict[str, str] | None = None,
    working_dir: str | Path | None = None,
) -> ResultCompactionTurn:
    artifacts: list[ResultCompactionArtifact] = []
    total_original = 0
    total_compacted = 0
    aggregate_budget = _resolve_aggregate_budget_chars(deps, len(results))
    # tool-result governance 单一确定性路径：truncate/preview/reference persistence，
    # 无独立 summarizer 分支。
    compression_enabled = False

    # We will build a replacement decision state to return
    replacement_state = ReplacementDecisionState()

    for call, result in zip(tool_calls, results, strict=False):
        cid = call.get("id", "")
        # Check if this tool call already has a frozen replacement
        if frozen_replacement_state and cid in frozen_replacement_state:
            # Replay the frozen replacement directly
            from prismclaw.schema.agent.data_types import TextBlock
            frozen_text = frozen_replacement_state[cid]
            result.content = [TextBlock(text=frozen_text)]

            original_chars = len(frozen_text) # We don't have the real original, treat frozen as original
            artifact = ResultCompactionArtifact(
                tool_call=call,
                result=result,
                material_type="generic_text",
                aggregate_priority=1,
                original_chars=original_chars,
                compacted_chars=len(frozen_text),
                strategies=["replay_frozen"],
                state_source_text=frozen_text
            )
        else:
            artifact = _compact_single_result(
                call=call,
                result=result,
                deps=deps,
                compression_enabled=compression_enabled,
                working_dir=working_dir,
            )

            # Record the replacement decision if it was compacted
            if artifact.compacted_chars < artifact.original_chars or "truncate" in artifact.strategies or "preview" in artifact.strategies:
                # Persisted results must replay the <persisted-output>
                # reference (not the original full text) so the frozen
                # turn keeps pointing at the recoverable file, not at a
                # re-inflated copy that would blow the context window.
                text_content = _result_text(artifact.result)

                from prismclaw.schema.content import ReplacementDecision
                # NOTE: ``artifact_type`` must be one of the
                # ``CompactionArtifactType`` literals defined in
                # ``prismclaw.kernel.agent`` because
                # ``ReplacementDecision.to_compaction_artifact()``
                # re-emits it as a ``CompactionArtifact``, which Pydantic
                # validates against the literal union. ``text_preview``
                # was a pre-literal legacy string that triggered
                # ``pydantic_core.ValidationError`` on the next turn's
                # ``tree.get_persisted_artifacts()`` call. This decision
                # is semantically a tool_result compaction (scope =
                # ``"tool_result"``), so use the canonical literal.
                replacement_state.decisions.append(
                    ReplacementDecision(
                        decision_id=cid,
                        scope="tool_result",
                        artifact_type="tool_result_compaction",
                        source_id=cid,
                        replacement_text=text_content,
                    )
                )

        artifacts.append(artifact)
        total_original += artifact.original_chars
        total_compacted += artifact.compacted_chars

    if total_compacted > aggregate_budget:
        for artifact in sorted(
            artifacts,
            key=lambda item: (item.result.is_error, item.aggregate_priority),
        ):
            policy = _policy_for(artifact.tool_call, artifact.result, deps)
            if total_compacted <= aggregate_budget:
                break
            tightened = _aggressively_compact(
                artifact.result,
                artifact.tool_call,
                policy=policy,
                original_chars=artifact.original_chars,
                state_source_text=artifact.state_source_text,
                deps=deps,
                working_dir=working_dir,
            )
            if tightened.compacted_chars >= artifact.compacted_chars:
                continue
            total_compacted -= artifact.compacted_chars - tightened.compacted_chars
            artifact.result = tightened.result
            artifact.compacted_chars = tightened.compacted_chars
            artifact.strategies.extend(
                step for step in tightened.strategies if step not in artifact.strategies
            )

    for artifact in artifacts:
        artifact.result = _attach_compaction_metadata(
            artifact.result,
            artifact.tool_call,
            material_type=artifact.material_type,
            original_chars=artifact.original_chars,
            compacted_chars=artifact.compacted_chars,
            strategies=artifact.strategies,
                state_source_text=artifact.state_source_text,
        )

    # Ensure replacement_state includes our recorded decisions
    final_replacement_state = _result_replacement_state(artifacts)

    for dec in replacement_state.decisions:
        # Check if already present to avoid duplicates
        existing = next((d for d in final_replacement_state.decisions if d.decision_id == dec.decision_id), None)
        if not existing:
            # Copy the freshly attached compaction artifact into the replacement ledger.
            for artifact in artifacts:
                if artifact.tool_call.get("id") == dec.source_id:
                    meta = _tool_result_compaction_payload(artifact.result)
                    if meta:
                        dec.reference_id = meta.get("reference_id")
                        dec.source_digest = meta.get("source_digest")
                        dec.material_type = meta.get("material_type")
                        dec.original_chars = meta.get("original_chars")
                        dec.compacted_chars = meta.get("compacted_chars")
                        dec.preview = meta.get("preview")
                        if "strategies" in meta:
                            dec.strategies = meta["strategies"]
            final_replacement_state.decisions.append(dec)
        else:
            # Update the replacement_text if it was recorded
            if dec.replacement_text and existing.replacement_text is None:
                existing.replacement_text = dec.replacement_text

    return ResultCompactionTurn(
        artifacts=artifacts,
        aggregate_budget_chars=aggregate_budget,
        total_original_chars=total_original,
        total_compacted_chars=total_compacted,
        replacement_state=final_replacement_state,
    )


async def _compact_tool_results_for_turn_async_impl(
    *,
    tool_calls: list[dict[str, Any]],
    results: list[ToolResult],
    deps: Any,
    frozen_replacement_state: dict[str, str] | None = None,
    working_dir: str | Path | None = None,
) -> ResultCompactionTurn:
    """Async variant of the per-turn compactor.

    Mirrors ``_compact_tool_results_for_turn_sync`` but awaits
    ``_compact_single_result_async`` so the LLM pass can run.  The
    post-compaction bookkeeping (replacement-state tracking, return
    shape) is identical to the sync path so callers do not need to
    branch on which path was used.
    """
    artifacts: list[ResultCompactionArtifact] = []
    total_original = 0
    total_compacted = 0
    aggregate_budget = _resolve_aggregate_budget_chars(deps, len(results))
    compression_enabled = False
    replacement_state = ReplacementDecisionState()

    for call, result in zip(tool_calls, results, strict=False):
        cid = call.get("id", "")
        if frozen_replacement_state and cid in frozen_replacement_state:
            from prismclaw.schema.agent.data_types import TextBlock
            frozen_text = frozen_replacement_state[cid]
            result.content = [TextBlock(text=frozen_text)]
            original_chars = len(frozen_text)
            artifact = ResultCompactionArtifact(
                tool_call=call,
                result=result,
                material_type="generic_text",
                aggregate_priority=1,
                original_chars=original_chars,
                compacted_chars=len(frozen_text),
                strategies=["replay_frozen"],
                state_source_text=frozen_text,
            )
        else:
            artifact = await _compact_single_result_async(
                call=call,
                result=result,
                deps=deps,
                compression_enabled=compression_enabled,
                working_dir=working_dir,
            )
        # Always feed the per-result strategies back to the replacement
        # state so the frozen-replay story stays consistent with the
        # sync path.
        from prismclaw.schema.content import (
            ReplacementDecision,
        )
        try:
            meta = replacement_decision_from_artifact(
                parse_compaction_artifact(_tool_result_compaction_payload(artifact.result))
            )
            if meta is None:
                raise ValueError("missing replacement decision metadata")
            dec = ReplacementDecision(
                decision_id=meta.decision_id,
                scope=meta.scope,
                artifact_type=meta.artifact_type,
                source_id=meta.source_id,
                reference_id=meta.reference_id,
                source_name=meta.source_name,
                source_digest=meta.source_digest,
                material_type=meta.material_type,
                original_chars=meta.original_chars,
                compacted_chars=meta.compacted_chars,
                preview=meta.preview,
                strategies=list(artifact.strategies or []),
                replacement_text=meta.replacement_text,
            )
        except Exception:
            dec = ReplacementDecision(
                decision_id=cid,
                scope="tool_result",
                artifact_type="tool_result_compaction",
                source_id=cid,
                original_chars=artifact.original_chars,
                compacted_chars=artifact.compacted_chars,
                strategies=list(artifact.strategies or []),
                replacement_text=None,
            )
        # A persisted result must replay its <persisted-output> reference,
        # not the original full text, so frozen turns keep pointing at the
        # recoverable file. The payload-derived replacement_text is None
        # for persisted results (no inline preview artifact), so override.
        if "per_result_persist" in artifact.strategies:
            dec.replacement_text = _result_text(artifact.result)
        replacement_state.decisions.append(dec)
        artifacts.append(artifact)
        total_original += artifact.original_chars
        total_compacted += artifact.compacted_chars

    return ResultCompactionTurn(
        artifacts=artifacts,
        aggregate_budget_chars=aggregate_budget,
        total_original_chars=total_original,
        total_compacted_chars=total_compacted,
        replacement_state=replacement_state,
    )


def compact_context_blocks_for_turn(
    *,
    blocks: list[ContextBlock],
    token_budget: int | None,
    compaction_config: Any | None = None,
    persisted_artifacts: dict[str, CompactionArtifact] | None = None,
) -> list[ContextBlock]:
    if not blocks:
        return []

    aggregate_budget = _resolve_context_block_aggregate_budget_chars(
        token_budget=token_budget,
        compaction_config=compaction_config,
    )
    artifacts: list[ContextBlockCompactionArtifact] = []
    total_compacted = 0

    for block in blocks:
        artifact = _compact_single_context_block(
            block=block,
            aggregate_budget_chars=aggregate_budget,
            block_count=len(blocks),
            persisted_artifacts=persisted_artifacts or {},
        )
        artifacts.append(artifact)
        total_compacted += artifact.compacted_chars

    if aggregate_budget > 0 and total_compacted > aggregate_budget:
        for artifact in sorted(artifacts, key=lambda item: item.aggregate_priority):
            if total_compacted <= aggregate_budget:
                break
            tightened = _aggressively_compact_context_block(
                artifact.block,
                material_type=artifact.material_type,
                original_chars=artifact.original_chars,
            )
            if tightened.compacted_chars >= artifact.compacted_chars:
                continue
            total_compacted -= artifact.compacted_chars - tightened.compacted_chars
            artifact.block = tightened.block
            artifact.compacted_chars = tightened.compacted_chars
            artifact.strategies.extend(
                step for step in tightened.strategies if step not in artifact.strategies
            )

    return [artifact.block for artifact in artifacts]


def _compact_single_result(
    *,
    call: dict[str, Any],
    result: ToolResult,
    deps: Any,
    compression_enabled: bool,
    working_dir: str | Path | None = None,
) -> ResultCompactionArtifact:
    """Sync version of the per-result compactor.

    When ``deps.config.context.compaction.tool_results.per_result_summary.enabled``
    is True AND the LLM summarizer (``deps.summarize_fn``) is
    wired, callers should prefer the async path
    ``_compact_single_result_async``.  This sync path always falls
    back to character truncation when the LLM pass is requested —
    the artifact will have the same shape, but no ``per_result_llm_summarize``
    strategy will be added.
    """
    raw_text = _result_text(result)
    original_chars = len(raw_text)
    policy = _policy_for(call, result, deps)
    strategies: list[str] = []

    # Single materialization decision (keep / persist / truncate). When a
    # result is persisted the inline content becomes a <persisted-output>
    # reference, so the post-compaction text passes below are skipped.
    compacted, mat_strategies = _materialize_single_result(
        result=result,
        inline_limit=policy.per_result_chars,
        working_dir=working_dir,
        deps=deps,
        call=call,
    )
    strategies.extend(mat_strategies)

    if "per_result_persist" not in mat_strategies:
        tool_name = ((call or {}).get("function") or {}).get("name") or ""
        tool_args = ((call or {}).get("function") or {}).get("arguments")
        if compression_enabled:
            compressed, stats = deps.ports.compress_tool_result(compacted, tool_name, tool_args)
            if stats.lossy:
                # SDD T-4: a lossy rule (head_tail middle-drop / regex line
                # removal) is about to rewrite an UN-persisted result —
                # persist the pre-compression original first so the dropped
                # lines stay recoverable, and point the model at the file.
                recovery = _persist_precompression_original(
                    result=compacted,
                    working_dir=working_dir,
                    deps=deps,
                )
                if recovery is not None:
                    merged = _result_text(compressed)
                    if merged:
                        merged = f"{merged}\n{recovery}"
                    else:
                        merged = recovery
                    compressed = ToolResult(
                        tool_call_id=compressed.tool_call_id,
                        content=[TextBlock(text=merged)],
                        is_error=compressed.is_error,
                        metadata=dict(compressed.metadata or {}),
                    )
                    strategies.append("precompress_persist")
            compacted = compressed
            if stats.saved_chars > 0:
                strategies.append("semantic_compress")

        semantic_text, semantic_strategies = _semantic_compact_text(
            _result_text(compacted),
            material_type=policy.material_type,
            max_chars=policy.per_result_chars,
        )
        if semantic_text and semantic_text != _result_text(compacted):
            compacted = ToolResult(
                tool_call_id=compacted.tool_call_id,
                content=[TextBlock(text=semantic_text)],
                is_error=compacted.is_error,
                metadata=dict(compacted.metadata or {}),
            )
        for strategy in semantic_strategies:
            if strategy not in strategies:
                strategies.append(strategy)

    compacted_chars = _result_text_len(compacted)
    return ResultCompactionArtifact(
        tool_call=call,
        result=compacted,
        material_type=policy.material_type,
        aggregate_priority=policy.aggregate_priority,
        original_chars=original_chars,
        compacted_chars=compacted_chars,
        strategies=strategies,
        state_source_text=raw_text,
    )


async def _compact_single_result_async(
    *,
    call: dict[str, Any],
    result: ToolResult,
    deps: Any,
    compression_enabled: bool,
    working_dir: str | Path | None = None,
) -> ResultCompactionArtifact:
    """Async version of ``_compact_single_result``.

    Performs the same offline work as the sync version, then — when
    ``per_result_summary.enabled`` is on and the result passes the
    gating checks — calls the LLM to produce a tighter summary.
    """
    artifact = _compact_single_result(
        call=call,
        result=result,
        deps=deps,
        compression_enabled=compression_enabled,
        working_dir=working_dir,
    )
    # A persisted result is already replaced by a <persisted-output>
    # reference; summarizing that reference is meaningless, so skip the
    # LLM pass.
    if "per_result_persist" in artifact.strategies:
        return artifact
    prs = _resolve_per_result_summary(deps)
    if prs is None:
        return artifact
    raw_text = _result_text(result)
    tool_name = ((call or {}).get("function") or {}).get("name") or ""
    skip_reason = _per_result_should_skip(
        original_chars=len(raw_text),
        tool_name=tool_name,
        material_type=artifact.material_type,
        prs=prs,
    )
    if skip_reason is not None:
        return artifact
    summarized = await run_per_result_fork_agent(
        raw_text=raw_text,
        tool_name=tool_name,
        deps=deps,
    )
    if not summarized:
        return artifact
    summarized_compacted = ToolResult(
        tool_call_id=artifact.result.tool_call_id,
        content=[TextBlock(text=summarized)],
        is_error=artifact.result.is_error,
        metadata={**(artifact.result.metadata or {}), "per_result_summarized": True},
    )
    return ResultCompactionArtifact(
        tool_call=call,
        result=summarized_compacted,
        material_type=artifact.material_type,
        aggregate_priority=artifact.aggregate_priority,
        original_chars=artifact.original_chars,
        compacted_chars=len(summarized),
        strategies=[*artifact.strategies, "per_result_llm_summarize"],
        state_source_text=raw_text,
    )


def _compact_single_context_block(
    *,
    block: ContextBlock,
    aggregate_budget_chars: int,
    block_count: int,
    persisted_artifacts: dict[str, CompactionArtifact],
) -> ContextBlockCompactionArtifact:
    original_chars = len(block.content)
    if not _should_compact_context_block(block) or original_chars <= 0:
        return ContextBlockCompactionArtifact(
            block=block,
            material_type=_detect_context_block_material_type(block),
            aggregate_priority=99,
            original_chars=original_chars,
            compacted_chars=original_chars,
            strategies=[],
        )

    policy = _context_block_policy_for(
        block=block,
        aggregate_budget_chars=aggregate_budget_chars,
        block_count=block_count,
    )
    strategies: list[str] = []
    persisted_artifact = _matching_context_block_artifact(block, persisted_artifacts)
    if persisted_artifact is not None:
        reused_block = _hydrate_context_block_from_artifact(block, persisted_artifact)
        return ContextBlockCompactionArtifact(
            block=reused_block,
            material_type=policy.material_type,
            aggregate_priority=policy.aggregate_priority,
            original_chars=original_chars,
            compacted_chars=len(reused_block.content),
            strategies=["frozen_reference_reuse", *persisted_artifact.strategies],
        )

    compacted_text = _truncate_text(block.content, policy.per_result_chars)
    if len(compacted_text) < original_chars:
        strategies.append("per_block_truncate")
    compacted_text, semantic_strategies = _semantic_compact_text(
        compacted_text,
        material_type=policy.material_type,
        max_chars=policy.per_result_chars,
    )
    for strategy in semantic_strategies:
        if strategy not in strategies:
            strategies.append(strategy)
    compacted_block = _attach_context_block_compaction_metadata(
        block,
        material_type=policy.material_type,
        original_chars=original_chars,
        compacted_text=compacted_text,
        strategies=strategies,
    )
    return ContextBlockCompactionArtifact(
        block=compacted_block,
        material_type=policy.material_type,
        aggregate_priority=policy.aggregate_priority,
        original_chars=original_chars,
        compacted_chars=len(compacted_text),
        strategies=strategies,
    )


def _aggressively_compact(
    result: ToolResult,
    tool_call: dict[str, Any],
    *,
    policy: ResultCompactionPolicy,
    original_chars: int,
    state_source_text: str,
    deps: Any = None,
    working_dir: str | Path | None = None,
) -> ResultCompactionArtifact:
    # Already a persisted <persisted-output> reference: tiny inline
    # footprint + Read-recoverable. Re-compacting would strip the
    # [Full output persisted to: <path>] pointer (aggressive_chars floor
    # 600 > the ~500-char reference skips the persist branch and falls into
    # _replace_with_reference_preview), breaking the recovery path. Leave
    # persisted references untouched — idempotent for any caller.
    if deps is not None and deps.ports.is_persisted_content(_result_text(result)):
        return ResultCompactionArtifact(
            tool_call=tool_call,
            result=result,
            material_type=policy.material_type,
            aggregate_priority=policy.aggregate_priority,
            original_chars=original_chars,
            compacted_chars=_result_text_len(result),
            strategies=[],  # keep the caller's existing strategies intact
            state_source_text=state_source_text,
        )
    # When the aggregated budget is exceeded, prefer writing the largest
    # results to disk (file reference) over blunt truncation — it shrinks
    # the context the most while keeping the data recoverable. Fall back to
    # the existing inline reference-preview when persistence is unavailable.
    if len(_result_text(result)) > policy.aggressive_chars:
        # Try working_dir persistence first
        persisted = None
        if working_dir is not None:
            persisted = _persist_reference(
                result=result,
                call=tool_call,
                deps=deps,
                working_dir=working_dir,
                reason="aggregate_persist",
            )
        # Fallback to temp directory
        if persisted is None and _tool_allows_persist(_tool_obj_for_call(tool_call, deps)):
            import tempfile
            tmp_dir = Path(tempfile.gettempdir()) / "prismclaw_tool_results"
            persisted = _persist_reference(
                result=result,
                call=tool_call,
                deps=deps,
                working_dir=tmp_dir,
                reason="aggregate_persist_tmp",
            )
        if persisted is not None:
            return ResultCompactionArtifact(
                tool_call=tool_call,
                result=persisted,
                material_type=policy.material_type,
                aggregate_priority=policy.aggregate_priority,
                original_chars=original_chars,
                compacted_chars=_result_text_len(persisted),
                strategies=["aggregate_budget_compact", "per_result_persist"],
                state_source_text=state_source_text,
            )

    compacted = truncate_tool_result(result, policy.aggressive_chars)
    compacted = _replace_with_reference_preview(
        compacted,
        tool_call,
        material_type=policy.material_type,
        original_chars=original_chars,
    )
    return ResultCompactionArtifact(
        tool_call=tool_call,
        result=compacted,
        material_type=policy.material_type,
        aggregate_priority=policy.aggregate_priority,
        original_chars=original_chars,
        compacted_chars=_result_text_len(compacted),
        strategies=["aggregate_budget_compact", "reference_preview"],
        state_source_text=state_source_text,
    )


def _aggressively_compact_context_block(
    block: ContextBlock,
    *,
    material_type: ResultMaterialType,
    original_chars: int,
) -> ContextBlockCompactionArtifact:
    replacement = _replace_context_block_with_reference_preview(
        block,
        material_type=material_type,
        original_chars=original_chars,
    )
    return ContextBlockCompactionArtifact(
        block=replacement,
        material_type=material_type,
        aggregate_priority=0,
        original_chars=original_chars,
        compacted_chars=max(
            replacement.metadata.get("projected_compacted_chars", 0),
            len(replacement.content),
        ),
        strategies=["aggregate_budget_compact", "reference_preview"],
    )


def _matching_context_block_artifact(
    block: ContextBlock,
    persisted_artifacts: dict[str, CompactionArtifact],
) -> CompactionArtifact | None:
    artifact = persisted_artifacts.get(block.id)
    if artifact is None or artifact.scope != "context_block":
        return None
    if artifact.source_name not in {None, "", block.source}:
        return None
    if artifact.source_digest and artifact.source_digest != _content_digest(block.content):
        return None
    if not artifact.replacement_text:
        return None
    return artifact


def _hydrate_context_block_from_artifact(
    block: ContextBlock,
    artifact: CompactionArtifact,
) -> ContextBlock:
    from prismclaw.engine.state.projection_rendering import build_result_compaction_placeholder
    strategies = list(artifact.strategies)
    if "frozen_reference_reuse" not in strategies:
        strategies.insert(0, "frozen_reference_reuse")
    compacted_text = artifact.replacement_text or block.content
    preview_text = artifact.preview
    if "reference_preview" in artifact.strategies:
        compacted_text = build_result_compaction_placeholder(artifact.reference_id or artifact.artifact_id)
        preview_text = artifact.preview
    return _attach_context_block_compaction_metadata(
        block,
        material_type=(artifact.material_type or "generic_text"),
        original_chars=max(artifact.original_chars or 0, len(block.content)),
        compacted_text=compacted_text,
        strategies=strategies,
        metadata={**dict(block.metadata or {}), "result_compaction": artifact.to_metadata_dict()},
        artifact_id=artifact.artifact_id,
        source_digest=artifact.source_digest or _content_digest(block.content),
        frozen=True,
        preview_text=preview_text,
    )


def _truncate_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    truncated = truncate_tool_result(
        ToolResult(tool_call_id="", content=[TextBlock(text=text)], is_error=False),
        max_chars,
    )
    return _result_text(truncated)


def _should_compact_context_block(block: ContextBlock) -> bool:
    if not block.truncatable or not block.content:
        return False
    if block.source in {"session_state", "history_compaction"}:
        return False
    if block.source.startswith("memory_"):
        return True
    return (block.metadata or {}).get("source_kind") == "attachment"
