from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Protocol

from prismclaw.schema.dream.dream_types import DreamAuditEntry, DreamBatchHealthMetrics
from prismclaw.schema.dream.dream import DreamGovernanceStore
from prismclaw.schema.dream.dream_types import DreamGovernanceRecord
from prismclaw.schema.dream.dream import DreamStateStore
from prismclaw.schema.dream.dream_types import DreamSessionState
from prismclaw.schema.storage.storage_types import MemoryStore

class DreamAuditReader(Protocol):
    async def list_entries(
        self,
        *,
        session_key: str | None = None,
        batch_id: str | None = None,
        phase: str | None = None,
    ) -> list[DreamAuditEntry]: ...

    async def latest_batch_metrics(
        self,
        *,
        trigger: str | None = None,
        session_key: str | None = None,
    ) -> DreamBatchHealthMetrics | None: ...

class DreamDispatchReader(Protocol):
    async def list_dead_letters(
        self,
        *,
        session_key: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, object]]: ...

    async def list_replay_candidates(
        self,
        *,
        session_key: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, object]]: ...

@dataclass(slots=True)
class DreamStateWatermarks:
    last_activity_at: float | None = None
    last_enqueue_activity_at: float | None = None
    last_enqueue_activity_status: str | None = None
    last_enqueue_nightly_at: float | None = None
    last_enqueue_nightly_status: str | None = None
    last_light_at: float | None = None
    last_deep_at: float | None = None
    last_rem_at: float | None = None
    last_success_at: float | None = None
    last_failure_at: float | None = None
    last_audit_at: float | None = None
    last_governance_update_at: float | None = None
    last_published_at: float | None = None
    last_nightly_window_started_at: float | None = None
    compile_cursor: str | None = None
    last_compile_started_at: float | None = None
    last_compile_finished_at: float | None = None

@dataclass(slots=True)
class DreamQualityMetrics:
    score: int
    health_status: str
    latest_batch_id: str | None = None
    latest_trigger: str | None = None
    latest_phase_count: int = 0
    latest_failed_phase_count: int = 0
    latest_total_llm_cost: int = 0
    latest_promoted_count: int = 0
    latest_deduplicated_count: int = 0
    latest_archived_count: int = 0
    latest_synthesized_count: int = 0
    latest_compiled_candidate_count: int = 0
    latest_extracted_candidate_count: int = 0
    latest_published_candidate_count: int = 0
    latest_discarded_candidate_count: int = 0
    latest_merged_entry_count: int = 0
    latest_temporal_update_count: int = 0
    latest_gate_rejected_entry_count: int = 0
    latest_published_memory_count: int = 0
    latest_failure_reason: str | None = None
    latest_dispatch_dead_letter_error: str | None = None
    latest_dispatch_replay_block_reason: str | None = None
    latest_compile_status: str | None = None

@dataclass(slots=True)
class DreamPublicationMetrics:
    total_records: int = 0
    pending_review_count: int = 0
    published_count: int = 0
    rejected_count: int = 0
    rolled_back_count: int = 0
    rollback_requested_count: int = 0
    shadow_count: int = 0
    canary_count: int = 0
    direct_count: int = 0
    live_count: int = 0
    shadow_pending_count: int = 0
    canary_pending_count: int = 0
    memory_record_count: int = 0
    artifact_record_count: int = 0
    dispatch_dead_letter_count: int = 0
    dispatch_replayable_dead_letter_count: int = 0
    dispatch_blocked_replay_count: int = 0
    archive_artifact_count: int = 0

@dataclass(slots=True)
class DreamStatusSnapshot:
    session_key: str
    state: str
    dream_pending: bool
    light_pending: bool
    nightly_pending: bool
    memory_dirty_score: int
    current_batch_id: str | None
    current_lock_owner: str | None
    watermarks: DreamStateWatermarks
    quality: DreamQualityMetrics
    publications: DreamPublicationMetrics

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

class DreamStatusService:
    def __init__(
        self,
        *,
        state_store: DreamStateStore,
        audit_log: DreamAuditReader | None = None,
        governance_store: DreamGovernanceStore | None = None,
        dispatch_reader: DreamDispatchReader | None = None,
        memory_store: MemoryStore | None = None,
    ) -> None:
        self._state_store = state_store
        self._audit_log = audit_log
        self._governance_store = governance_store
        self._dispatch_reader = dispatch_reader
        self._memory_store = memory_store

    async def get_status(self, session_key: str) -> DreamStatusSnapshot | None:
        state = await self._state_store.get(session_key)
        audit_entries = (
            await self._audit_log.list_entries(session_key=session_key) if self._audit_log else []
        )
        governance_records = (
            await self._governance_store.list_records(session_key=session_key, limit=500)
            if self._governance_store
            else []
        )
        dead_letters = (
            await self._dispatch_reader.list_dead_letters(session_key=session_key, limit=50)
            if self._dispatch_reader
            else []
        )
        replay_candidates = (
            await self._dispatch_reader.list_replay_candidates(session_key=session_key, limit=50)
            if self._dispatch_reader and hasattr(self._dispatch_reader, "list_replay_candidates")
            else []
        )
        archive_stats = (
            await self._memory_store.get_archive_storage_stats(session_key)
            if self._memory_store is not None
            else {}
        )
        if state is None and not audit_entries and not governance_records and not dead_letters:
            return None
        return _build_status_snapshot(
            session_key=session_key,
            state=state,
            audit_entries=audit_entries,
            latest_batch=(
                await self._audit_log.latest_batch_metrics(session_key=session_key)
                if self._audit_log
                else None
            ),
            governance_records=governance_records,
            dead_letters=dead_letters,
            replay_candidates=replay_candidates,
            archive_stats=archive_stats,
        )

    async def list_statuses(self, session_key: str | None = None) -> list[DreamStatusSnapshot]:
        if session_key is not None:
            snapshot = await self.get_status(session_key)
            return [snapshot] if snapshot is not None else []

        states = await self._state_store.list_states()
        session_keys = {state.session_key for state in states}
        if self._audit_log is not None:
            audit_entries_all = await self._audit_log.list_entries()
            session_keys.update(entry.session_key for entry in audit_entries_all)
        if self._governance_store is not None:
            governance_all = await self._governance_store.list_records(limit=1000)
            session_keys.update(record.session_key for record in governance_all)
        if self._dispatch_reader is not None:
            dead_letters = await self._dispatch_reader.list_dead_letters(limit=1000)
            session_keys.update(
                str(item.get("session_key", "") or "").strip()
                for item in dead_letters
                if str(item.get("session_key", "") or "").strip()
            )
        items: list[DreamStatusSnapshot] = []
        for current_session_key in sorted(session_keys):
            snapshot = await self.get_status(current_session_key)
            if snapshot is not None:
                items.append(snapshot)
        return items

def _build_status_snapshot(
    *,
    session_key: str,
    state: DreamSessionState | None,
    audit_entries: list[DreamAuditEntry],
    latest_batch: DreamBatchHealthMetrics | None,
    governance_records: list[DreamGovernanceRecord],
    dead_letters: list[dict[str, object]],
    replay_candidates: list[dict[str, object]],
    archive_stats: dict[str, int],
) -> DreamStatusSnapshot:
    watermarks = _build_watermarks(state, audit_entries, governance_records)
    publications = _build_publication_metrics(
        governance_records,
        dead_letters=dead_letters,
        replay_candidates=replay_candidates,
        archive_stats=archive_stats,
    )
    quality = _build_quality_metrics(
        state=state,
        latest_batch=latest_batch,
        audit_entries=audit_entries,
        publications=publications,
        watermarks=watermarks,
        dead_letters=dead_letters,
        replay_candidates=replay_candidates,
    )
    return DreamStatusSnapshot(
        session_key=session_key,
        state=_derive_state_label(state, quality, publications),
        dream_pending=bool(getattr(state, "dream_pending", False)),
        light_pending=bool(getattr(state, "light_pending", False)),
        nightly_pending=bool(getattr(state, "nightly_pending", False)),
        memory_dirty_score=int(getattr(state, "memory_dirty_score", 0) or 0),
        current_batch_id=getattr(state, "dream_batch_id", None),
        current_lock_owner=getattr(state, "dream_lock_owner", None),
        watermarks=watermarks,
        quality=quality,
        publications=publications,
    )

def _build_watermarks(
    state: DreamSessionState | None,
    audit_entries: list[DreamAuditEntry],
    governance_records: list[DreamGovernanceRecord],
) -> DreamStateWatermarks:
    ok_entries = [entry for entry in audit_entries if entry.status == "ok"]
    failed_entries = [entry for entry in audit_entries if entry.status != "ok"]
    published_records = [
        record for record in governance_records if record.review_status == "published"
    ]
    return DreamStateWatermarks(
        last_activity_at=_none_if_zero(getattr(state, "last_activity_at", None)),
        last_enqueue_activity_at=getattr(state, "last_enqueue_activity_at", None),
        last_enqueue_activity_status=getattr(state, "last_enqueue_activity_status", None),
        last_enqueue_nightly_at=getattr(state, "last_enqueue_nightly_at", None),
        last_enqueue_nightly_status=getattr(state, "last_enqueue_nightly_status", None),
        last_light_at=getattr(state, "last_light_at", None),
        last_deep_at=getattr(state, "last_deep_at", None),
        last_rem_at=getattr(state, "last_rem_at", None),
        last_success_at=max((entry.ended_at for entry in ok_entries), default=None),
        last_failure_at=max((entry.ended_at for entry in failed_entries), default=None),
        last_audit_at=max((entry.ended_at for entry in audit_entries), default=None),
        last_governance_update_at=max(
            (record.updated_at for record in governance_records),
            default=None,
        ),
        last_published_at=max(
            max((record.updated_at for record in published_records), default=None) or 0.0,
            getattr(state, "publish_watermark_at", None) or 0.0,
        )
        or None,
        last_nightly_window_started_at=getattr(state, "last_nightly_window_started_at", None),
        compile_cursor=getattr(state, "compile_cursor", None),
        last_compile_started_at=getattr(state, "last_compile_started_at", None),
        last_compile_finished_at=getattr(state, "last_compile_finished_at", None),
    )

def _build_publication_metrics(
    governance_records: list[DreamGovernanceRecord],
    dead_letters: list[dict[str, object]],
    replay_candidates: list[dict[str, object]],
    archive_stats: dict[str, int],
) -> DreamPublicationMetrics:
    return DreamPublicationMetrics(
        total_records=len(governance_records),
        pending_review_count=sum(
            1 for record in governance_records if record.review_status == "pending_review"
        ),
        published_count=sum(
            1 for record in governance_records if record.review_status == "published"
        ),
        rejected_count=sum(
            1 for record in governance_records if record.review_status == "rejected"
        ),
        rolled_back_count=sum(
            1 for record in governance_records if record.review_status == "rolled_back"
        ),
        rollback_requested_count=sum(
            1 for record in governance_records if record.review_status == "rollback_requested"
        ),
        shadow_count=sum(1 for record in governance_records if record.shadow),
        canary_count=sum(1 for record in governance_records if record.publish_channel == "canary"),
        direct_count=sum(1 for record in governance_records if record.publish_channel == "direct"),
        live_count=sum(1 for record in governance_records if record.rollout_stage == "live"),
        shadow_pending_count=sum(
            1
            for record in governance_records
            if record.publish_channel == "shadow" and record.review_status == "pending_review"
        ),
        canary_pending_count=sum(
            1
            for record in governance_records
            if record.publish_channel == "canary" and record.review_status == "pending_review"
        ),
        memory_record_count=sum(1 for record in governance_records if record.target_type == "memory"),
        artifact_record_count=sum(1 for record in governance_records if record.target_type == "artifact"),
        dispatch_dead_letter_count=len(dead_letters),
        dispatch_replayable_dead_letter_count=sum(
            1 for item in replay_candidates if item.get("replayable") is True
        ),
        dispatch_blocked_replay_count=sum(
            1 for item in replay_candidates if item.get("replayable") is not True
        ),
        archive_artifact_count=int(archive_stats.get("archive_artifact_count", 0) or 0),
    )

def _build_quality_metrics(
    *,
    state: DreamSessionState | None,
    latest_batch: DreamBatchHealthMetrics | None,
    audit_entries: list[DreamAuditEntry],
    publications: DreamPublicationMetrics,
    watermarks: DreamStateWatermarks,
    dead_letters: list[dict[str, object]],
    replay_candidates: list[dict[str, object]],
) -> DreamQualityMetrics:
    latest_failure_reason = None
    if audit_entries:
        latest_failure_reason = next(
            (entry.failure_reason for entry in reversed(audit_entries) if entry.failure_reason),
            None,
        )
    score = 100
    if latest_batch is None:
        score -= 15
    else:
        score -= min(40, latest_batch.failed_phase_count * 20)
        if latest_batch.total_llm_cost > 0:
            score -= min(10, latest_batch.total_llm_cost // 5000)
    if watermarks.last_failure_at is not None and (
        watermarks.last_success_at is None or watermarks.last_failure_at >= watermarks.last_success_at
    ):
        score -= 15
    if publications.pending_review_count > 0:
        score -= min(15, publications.pending_review_count)
    if publications.dispatch_dead_letter_count > 0:
        score -= min(25, publications.dispatch_dead_letter_count * 5)
    if getattr(state, "dream_lock_owner", None):
        score -= 5
    if getattr(state, "dream_pending", False):
        score -= 5
    score = max(0, min(100, score))
    return DreamQualityMetrics(
        score=score,
        health_status=_derive_health_status(
            state=state,
            latest_batch=latest_batch,
            publications=publications,
            watermarks=watermarks,
        ),
        latest_batch_id=getattr(latest_batch, "batch_id", None),
        latest_trigger=getattr(latest_batch, "trigger", None),
        latest_phase_count=int(getattr(latest_batch, "phase_count", 0) or 0),
        latest_failed_phase_count=int(getattr(latest_batch, "failed_phase_count", 0) or 0),
        latest_total_llm_cost=int(getattr(latest_batch, "total_llm_cost", 0) or 0),
        latest_promoted_count=int(getattr(latest_batch, "promoted_count", 0) or 0),
        latest_deduplicated_count=int(getattr(latest_batch, "deduplicated_count", 0) or 0),
        latest_archived_count=int(getattr(latest_batch, "archived_count", 0) or 0),
        latest_synthesized_count=int(getattr(latest_batch, "synthesized_count", 0) or 0),
        latest_compiled_candidate_count=int(getattr(latest_batch, "compiled_candidates", 0) or 0),
        latest_extracted_candidate_count=int(getattr(latest_batch, "extracted_candidates", 0) or 0),
        latest_published_candidate_count=int(getattr(latest_batch, "published_candidates", 0) or 0),
        latest_discarded_candidate_count=int(getattr(latest_batch, "discarded_candidates", 0) or 0),
        latest_merged_entry_count=int(getattr(latest_batch, "merged_entries", 0) or 0),
        latest_temporal_update_count=int(getattr(latest_batch, "temporal_updates", 0) or 0),
        latest_gate_rejected_entry_count=int(getattr(latest_batch, "gate_rejected_entries", 0) or 0),
        latest_published_memory_count=int(getattr(latest_batch, "published_memory_count", 0) or 0),
        latest_failure_reason=latest_failure_reason,
        latest_dispatch_dead_letter_error=_latest_dispatch_dead_letter_error(dead_letters),
        latest_dispatch_replay_block_reason=_latest_dispatch_replay_block_reason(replay_candidates),
        latest_compile_status=getattr(state, "last_compile_status", None),
    )

def _derive_health_status(
    *,
    state: DreamSessionState | None,
    latest_batch: DreamBatchHealthMetrics | None,
    publications: DreamPublicationMetrics,
    watermarks: DreamStateWatermarks,
) -> str:
    if getattr(state, "dream_lock_owner", None):
        return "running"
    if latest_batch is not None and latest_batch.failed_phase_count > 0:
        return "degraded"
    if publications.dispatch_dead_letter_count > 0:
        return "degraded"
    if watermarks.last_failure_at is not None and (
        watermarks.last_success_at is None or watermarks.last_failure_at >= watermarks.last_success_at
    ):
        return "degraded"
    if publications.pending_review_count > 0:
        return "review_pending"
    if getattr(state, "dream_pending", False):
        return "queued"
    return "healthy"

def _derive_state_label(
    state: DreamSessionState | None,
    quality: DreamQualityMetrics,
    publications: DreamPublicationMetrics,
) -> str:
    if getattr(state, "dream_lock_owner", None):
        return "running"
    if quality.health_status == "degraded":
        return "degraded"
    if publications.pending_review_count > 0:
        return "review_pending"
    if getattr(state, "dream_pending", False):
        return "queued"
    return "idle"

def _none_if_zero(value: float | None) -> float | None:
    if value in (None, 0, 0.0):
        return None
    return float(value)

def _latest_dispatch_dead_letter_error(dead_letters: list[dict[str, object]]) -> str | None:
    if not dead_letters:
        return None
    latest = max(
        dead_letters,
        key=lambda item: float(item.get("enqueued_at", 0.0) or 0.0),
    )
    text = str(latest.get("last_error", "") or "").strip()
    return text or None

def _latest_dispatch_replay_block_reason(
    replay_candidates: list[dict[str, object]],
) -> str | None:
    blocked = [
        item
        for item in replay_candidates
        if item.get("replayable") is not True
        and str(item.get("replay_block_reason", "") or "").strip()
    ]
    if not blocked:
        return None
    latest = max(
        blocked,
        key=lambda item: float(item.get("enqueued_at", 0.0) or 0.0),
    )
    text = str(latest.get("replay_block_reason", "") or "").strip()
    return text or None
