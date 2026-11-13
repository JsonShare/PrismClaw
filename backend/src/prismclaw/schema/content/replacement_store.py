from __future__ import annotations

import logging
from typing import Any

from prismclaw.schema.content import (
    ReplacementDecision,
    ReplacementDecisionState,
)
from prismclaw.schema.content.compaction import CompactionArtifact, parse_compaction_artifact
from prismclaw.schema.content.context import ContextBlock
from prismclaw.schema.session import (
    REPLACEMENT_DECISION_CUSTOM_TYPE,
    RESULT_COMPACTION_REFERENCE_CUSTOM_TYPE,
)

logger = logging.getLogger(__name__)


def collect_replacement_decision_state(branch: list[Any]) -> ReplacementDecisionState:
    explicit: list[ReplacementDecision] = []
    legacy: list[ReplacementDecision] = []
    for entry in branch:
        if getattr(entry, "type", None) != "custom":
            continue
        custom_type = getattr(entry, "custom_type", None)
        data = getattr(entry, "data", None)
        if custom_type == REPLACEMENT_DECISION_CUSTOM_TYPE:
            decision = parse_replacement_decision(data)
            if decision is not None:
                explicit.append(decision)
            continue
        if custom_type != RESULT_COMPACTION_REFERENCE_CUSTOM_TYPE:
            continue
        artifact = parse_compaction_artifact(data)
        decision = replacement_decision_from_artifact(artifact)
        if decision is not None:
            legacy.append(decision)
    if not explicit:
        state = ReplacementDecisionState(decisions=legacy)
    elif not legacy:
        state = ReplacementDecisionState(decisions=explicit)
    else:
        state = ReplacementDecisionState(decisions=[*legacy, *explicit])
    logger.debug(
        "replacement state collected: branch_entries=%d explicit=%d legacy=%d unique_sources=%d digest=%s",
        len(branch), len(explicit), len(legacy), state.decision_count(), state.state_digest(),
    )
    return state


def replacement_decision_from_artifact(
    artifact: CompactionArtifact | None,
) -> ReplacementDecision | None:
    if artifact is None:
        logger.debug("replacement decision skipped: artifact is None")
        return None
    decision = ReplacementDecision.from_artifact(artifact)
    if decision is None:
        logger.debug(
            "replacement decision skipped: from_artifact rejected artifact_id=%s scope=%s source_id=%s",
            artifact.artifact_id, artifact.scope, artifact.source_id or artifact.tool_call_id,
        )
    return decision


def parse_replacement_decision(data: Any) -> ReplacementDecision | None:
    if isinstance(data, ReplacementDecision):
        return data
    if not isinstance(data, dict):
        logger.debug("replacement decision skipped: data is %s, not dict", type(data).__name__)
        return None
    try:
        return ReplacementDecision.model_validate(data)
    except Exception:
        # 数据损坏的排查盲区：payload 校验失败会被上层静默丢弃，这里必须留痕。
        keys = sorted(data.keys()) if isinstance(data, dict) else []
        logger.warning(
            "replacement decision payload failed validation (dropped): keys=%s",
            keys,
            exc_info=True,
        )
        return None


def extract_replacement_decisions_from_blocks(
    blocks: list[ContextBlock],
) -> ReplacementDecisionState:
    decisions: list[ReplacementDecision] = []
    for block in blocks:
        metadata = dict(block.metadata or {})
        artifact = parse_compaction_artifact(metadata.get("result_compaction"))
        decision = replacement_decision_from_artifact(artifact)
        if decision is not None:
            decisions.append(decision)
    state = ReplacementDecisionState(decisions=decisions)
    logger.debug(
        "replacement decisions extracted from blocks: blocks=%d decisions=%d",
        len(blocks), len(decisions),
    )
    return state


def append_replacement_decisions(
    current: ReplacementDecisionState | None,
    update: ReplacementDecisionState | None,
) -> ReplacementDecisionState:
    base = current or ReplacementDecisionState()
    if update is None:
        return base
    merged = base.merged_with(update)
    logger.debug(
        "replacement states merged: base=%d update=%d merged=%d",
        base.decision_count(), update.decision_count(), merged.decision_count(),
    )
    return merged
