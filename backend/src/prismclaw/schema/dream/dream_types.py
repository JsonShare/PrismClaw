"""Dream subsystem type definitions — pure data, no behaviour.

Lives in contracts (L0) so that Protocol definitions can reference these
types without depending on engine (L2).  See SDD §5.1.2 + §5.1.4.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal
import time
import uuid

# ── Literal types ──

DreamCandidateKind = Literal["turn_context", "session_episode"]
DreamCandidateStatus = Literal["draft", "published", "discarded"]
DreamPublishState = Literal["draft", "reviewed", "canary", "published", "rejected"]
DreamTargetType = Literal["memory", "artifact", "compression"]
DreamReviewStatus = Literal["pending_review", "published", "rolled_back", "rollback_requested", "rejected"]
DreamPublishChannel = Literal["direct", "shadow", "canary"]
DreamRolloutStage = Literal["shadow", "canary", "live", "rolled_back", "rollback_requested", "rejected"]
DreamAuditStatus = Literal["ok", "failed"]
DreamMemoryKind = Literal["fact", "preference", "constraint", "procedure", "episode", "pattern"]
DreamArtifactKind = Literal["archive", "pattern", "graph", "relation"]


# ── Data containers ──

@dataclass(slots=True)
class DreamMemoryCandidate:
    candidate_id: str
    session_key: str
    session_id: str
    kind: DreamCandidateKind
    content: str
    source_entry_ids: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    status: DreamCandidateStatus = "draft"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DreamSessionState:
    session_key: str
    dream_pending: bool = False
    memory_dirty_score: int = 0
    last_activity_at: float = 0.0
    last_light_at: float | None = None
    last_deep_at: float | None = None
    last_rem_at: float | None = None
    dream_batch_id: str | None = None
    dream_lock_owner: str | None = None
    light_pending: bool = False
    nightly_pending: bool = False
    last_nightly_window_started_at: float | None = None
    compile_cursor: str | None = None
    last_compile_started_at: float | None = None
    last_compile_finished_at: float | None = None
    publish_watermark_at: float | None = None
    last_compiled_candidate_count: int = 0
    last_published_memory_count: int = 0
    last_gate_rejected_entries: int = 0
    last_compile_status: str | None = None
    last_enqueue_activity_at: float | None = None
    last_enqueue_activity_status: str | None = None
    last_enqueue_activity_detail: str | None = None
    last_enqueue_nightly_at: float | None = None
    last_enqueue_nightly_status: str | None = None
    last_enqueue_nightly_detail: str | None = None


@dataclass(slots=True)
class DreamAuditEntry:
    session_key: str
    batch_id: str
    phase: str
    trigger: str
    status: DreamAuditStatus
    started_at: float
    ended_at: float
    duration_s: float
    promoted_count: int = 0
    deduplicated_count: int = 0
    archived_count: int = 0
    synthesized_count: int = 0
    compiled_candidates: int = 0
    extracted_candidates: int = 0
    published_candidates: int = 0
    discarded_candidates: int = 0
    merged_entries: int = 0
    temporal_updates: int = 0
    gate_rejected_entries: int = 0
    published_memory_count: int = 0
    llm_cost: int = 0
    failure_reason: str | None = None


@dataclass(slots=True)
class DreamGovernanceRecord:
    record_id: str
    session_key: str
    target_type: DreamTargetType
    target_id: str
    publish_state: DreamPublishState
    review_status: DreamReviewStatus
    publish_channel: DreamPublishChannel = "direct"
    rollout_stage: DreamRolloutStage = "live"
    shadow: bool = False
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DreamDecisionAuditRecord:
    decision_id: str
    batch_id: str
    session_key: str
    record_id: str
    target_id: str
    action: str
    reason: str
    health_status: str
    rollout_percentage: int
    trigger: str
    dry_run: bool
    applied: bool
    created_at: float


# ── Dispatch boundary types ──

@dataclass(frozen=True, slots=True)
class DreamEnqueueResult:
    status: Literal["accepted", "unavailable", "failed"]
    operation: Literal["mark_activity", "enqueue_nightly"]
    session_key: str
    detail: str | None = None

    @property
    def accepted(self) -> bool:
        return self.status == "accepted"


# ── ID generation helpers (pure domain logic, not I/O) ──

def build_governance_record_id(prefix: str = "dream-review") -> str:
    """Generate a unique governance record ID."""
    return f"{prefix}:{uuid.uuid4().hex[:16]}"


# ── Batch health metrics (pure data, moved from persistence) ──

@dataclass(slots=True)
class DreamBatchHealthMetrics:
    batch_id: str
    trigger: str
    started_at: float
    ended_at: float
    duration_s: float
    session_count: int
    phase_count: int
    ok_phase_count: int
    failed_phase_count: int
    promoted_count: int
    deduplicated_count: int
    archived_count: int
    synthesized_count: int
    compiled_candidates: int
    extracted_candidates: int
    published_candidates: int
    discarded_candidates: int
    merged_entries: int
    temporal_updates: int
    gate_rejected_entries: int
    published_memory_count: int
    total_llm_cost: int
