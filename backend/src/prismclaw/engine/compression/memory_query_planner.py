from __future__ import annotations

from typing import Literal

from prismclaw.schema.agent.task_types import TaskSpec
from prismclaw.schema.content.context import ContextBlock, TaskProfile
from prismclaw.schema.content.evidence import EvidenceStrategy

TemporalMode = Literal["current_only", "include_history"]


def build_evidence_strategy(
    *,
    session_id: str,
    retrieval_query: str | None,
    task_profile: TaskProfile,
    request_mode: str | None,
    merged_state_payload: dict[str, object] | None,
    anchor_blocks: list[ContextBlock],
    task_spec: TaskSpec | None,
    l2_quota: int,
    l3_quota: int,
    archive_limit: int,
    graph_available: bool = False,
) -> EvidenceStrategy:
    strict_grounding = bool(task_profile.requires_grounding)
    prefers_tooling = bool(task_profile.prefers_tooling)

    memory_layers = ["L2", "L3"]
    per_layer_limits = {"L2": max(0, int(l2_quota)), "L3": max(0, int(l3_quota))}
    artifact_kinds = ["archive", "pattern", "graph", "relation"]
    # SDD D10：graph 扩展由真实能力决定（memory_graph_enabled），不再无条件
    # 宣称 enabled——开关关闭时策略不再谎报图谱能力。
    graph_expansion_enabled = graph_available
    temporal_mode: TemporalMode = "current_only"
    notes: list[str] = []
    strategy = "balanced"
    notes.append("mixed_task_plan")

    if strict_grounding:
        graph_expansion_enabled = graph_available
        artifact_kinds = _merge_unique(artifact_kinds, ["relation", "graph"])
        notes.append("grounding_required")
    if prefers_tooling:
        per_layer_limits["L3"] = max(per_layer_limits["L3"], 2)
        notes.append("tooling_bias")
    if task_profile.risk_level == "high":
        temporal_mode = "include_history"
        artifact_kinds = _merge_unique(artifact_kinds, ["archive", "relation"])
        notes.append("high_risk_include_history")
    if task_spec is not None:
        # 检索策略：prefers_tooling → 流程优先（执行/编辑/创建），
        # 否则 fact_episode（事实/案例优先，覆盖 answer/analyze/mixed）。
        if task_profile.prefers_tooling:
            strategy = "procedure_first"
        elif task_profile.requires_grounding:
            strategy = "fact_episode_first"
        if task_spec.risk_level == "high" and temporal_mode != "include_history":
            temporal_mode = "include_history"
            notes.append("task_spec_high_risk_include_history")
        if task_spec.constraints:
            notes.append(f"task_constraints:{min(len(task_spec.constraints), 4)}")

    evidence_types = ["memory_entry"]
    if archive_limit > 0:
        evidence_types.append("archive_entry")
    if graph_expansion_enabled:
        evidence_types.append("graph_relation")
    if strict_grounding:
        evidence_types.append("grounding_evidence")
    if request_mode:
        notes.append(f"request_mode:{request_mode}")

    return EvidenceStrategy(
        strategy_id=f"{session_id}:ep:{_plan_digest(retrieval_query, task_profile, request_mode)}",
        session_id=session_id,
        query_text=retrieval_query,
        strategy=strategy,
        memory_layers=memory_layers,
        per_layer_limits=per_layer_limits,
        archive_limit=max(0, int(archive_limit)),
        artifact_kinds=artifact_kinds,
        graph_expansion_enabled=graph_expansion_enabled,
        temporal_mode=temporal_mode,
        task_id=task_spec.task_id if task_spec is not None else None,
        evidence_types=_merge_unique(
            evidence_types,
            [
                *[block.source for block in anchor_blocks if block.source],
            ],
        ),
        anchor_sources=[block.source for block in anchor_blocks if block.source],
        merged_state_payload=dict(merged_state_payload or {}),
        notes=notes,
    )


def build_memory_query_plan(
    *,
    session_id: str,
    retrieval_query: str | None,
    task_profile: TaskProfile,
    request_mode: str | None,
    merged_state_payload: dict[str, object] | None,
    anchor_blocks: list[ContextBlock],
    task_spec: TaskSpec | None,
    l2_quota: int,
    l3_quota: int,
    archive_limit: int,
) -> EvidenceStrategy:
    return build_evidence_strategy(
        session_id=session_id,
        retrieval_query=retrieval_query,
        task_profile=task_profile,
        request_mode=request_mode,
        merged_state_payload=merged_state_payload,
        anchor_blocks=anchor_blocks,
        task_spec=task_spec,
        l2_quota=l2_quota,
        l3_quota=l3_quota,
        archive_limit=archive_limit,
    )


def _merge_unique(current: list[str], extra: list[str]) -> list[str]:
    merged = list(current)
    for item in extra:
        if item not in merged:
            merged.append(item)
    return merged


def _plan_digest(
    retrieval_query: str | None,
    task_profile: TaskProfile,
    request_mode: str | None,
) -> str:
    payload = "|".join(
        [
            retrieval_query or "",
            str(task_profile.risk_level),
            str(task_profile.prefers_tooling),
            str(task_profile.requires_grounding),
            request_mode or "",
        ]
    )
    import hashlib

    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]
