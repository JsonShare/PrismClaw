"""Dream subsystem Protocol definitions — behavioural contracts only.

Lives in contracts (L0) so that engine (L2) can depend on these interfaces
without circular imports.  All Protocols are ``@runtime_checkable`` for
duck-typing compatibility checks.  See SDD §5.1.2 + §5.1.4.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from prismclaw.schema.dream.dream_types import (
    DreamAuditEntry,
    DreamCandidateKind,
    DreamCandidateStatus,
    DreamDecisionAuditRecord,
    DreamGovernanceRecord,
    DreamMemoryCandidate,
    DreamPublishChannel,
    DreamPublishState,
    DreamReviewStatus,
    DreamRolloutStage,
    DreamSessionState,
    DreamTargetType,
)
from prismclaw.schema.scheduler.scheduler import SchedulerDispatchJob


@runtime_checkable
class DreamCandidateStore(Protocol):
    async def upsert(self, candidate: DreamMemoryCandidate) -> None: ...

    async def get_candidate(self, candidate_id: str) -> DreamMemoryCandidate | None: ...

    async def list_candidates(
        self,
        session_key: str,
        *,
        kind: DreamCandidateKind | None = None,
        status: DreamCandidateStatus | None = "draft",
        limit: int = 100,
    ) -> list[DreamMemoryCandidate]: ...


@runtime_checkable
class DreamStateStore(Protocol):
    async def get(self, session_key: str) -> DreamSessionState | None: ...

    async def put(self, state: DreamSessionState) -> None: ...

    async def list_states(self) -> list[DreamSessionState]: ...


@runtime_checkable
class DreamAuditLogger(Protocol):
    async def record(self, entry: DreamAuditEntry) -> None: ...


@runtime_checkable
class DreamGovernanceStore(Protocol):
    async def record(self, record: DreamGovernanceRecord) -> None: ...

    async def get(self, record_id: str) -> DreamGovernanceRecord | None: ...

    async def list_records(
        self,
        *,
        session_key: str | None = None,
        target_type: DreamTargetType | None = None,
        target_id: str | None = None,
        review_status: DreamReviewStatus | None = None,
        publish_channel: DreamPublishChannel | None = None,
        rollout_stage: DreamRolloutStage | None = None,
        limit: int = 100,
    ) -> list[DreamGovernanceRecord]: ...

    async def update_status(
        self,
        record_id: str,
        *,
        publish_state: DreamPublishState | None = None,
        review_status: DreamReviewStatus | None = None,
        metadata_updates: dict[str, Any] | None = None,
    ) -> DreamGovernanceRecord | None: ...


@runtime_checkable
class DreamDecisionAuditStore(Protocol):
    async def record(self, entry: DreamDecisionAuditRecord) -> None: ...

    async def list_entries(
        self,
        *,
        session_key: str | None = None,
        batch_id: str | None = None,
        limit: int = 100,
    ) -> list[DreamDecisionAuditRecord]: ...


# ── Admin / Governance operational Protocols ─────────────────────────
# Moved from engine/dream/governance/ops.py — consumed by app layer.


@runtime_checkable
class DreamGovernanceOps(Protocol):
    async def list_pending_reviews(self, session_key: str | None = None) -> list[DreamGovernanceRecord]: ...

    async def list_governance_records(
        self,
        *,
        session_key: str | None = None,
        review_status: str | None = None,
        publish_channel: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        limit: int = 100,
    ) -> list[DreamGovernanceRecord]: ...

    async def get_governance_summary(self, *, session_key: str | None = None) -> dict[str, Any]: ...

    async def plan_canary_actions(
        self,
        *,
        session_key: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]: ...

    async def reconcile_canary_actions(
        self,
        *,
        session_key: str | None = None,
        limit: int = 100,
        dry_run: bool = False,
    ) -> dict[str, Any]: ...

    async def list_canary_decision_audits(
        self,
        *,
        session_key: str | None = None,
        batch_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]: ...


@runtime_checkable
class DreamArchiveOps(Protocol):
    async def get_archive_storage(self, session_key: str) -> dict[str, Any]: ...


@runtime_checkable
class DreamStatusOps(Protocol):
    async def list_statuses(self, session_key: str | None = None) -> list[dict[str, Any]]: ...


@runtime_checkable
class DreamDispatchOps(Protocol):
    async def get_dispatch_status(self) -> dict[str, Any]: ...

    async def list_dispatch_dead_letters(
        self,
        *,
        session_key: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]: ...

    async def list_dispatch_audit_events(
        self,
        *,
        session_key: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]: ...

    async def replay_dispatch_dead_letter(self, stream_id: str) -> dict[str, Any]: ...

    async def list_dispatch_replay_candidates(
        self,
        *,
        session_key: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]: ...

    async def replay_dispatch_dead_letters(
        self,
        *,
        session_key: str | None = None,
        limit: int = 50,
        dry_run: bool = False,
    ) -> dict[str, Any]: ...


@runtime_checkable
class DreamReviewDecisionOps(Protocol):
    async def approve(
        self,
        record_id: str,
        *,
        reviewer: str = "",
        comment: str = "",
        expected_target_id: str | None = None,
        expected_evidence_fingerprint: str | None = None,
    ) -> DreamGovernanceRecord | None: ...

    async def reject(
        self,
        record_id: str,
        *,
        reviewer: str = "",
        comment: str = "",
        reason: str = "",
        expected_target_id: str | None = None,
        expected_evidence_fingerprint: str | None = None,
    ) -> DreamGovernanceRecord | None: ...

    async def rollback_artifact(self, record_id: str, *, reason: str = "") -> bool: ...

    async def rollback_memory(self, session_key: str, memory_id: str) -> bool: ...


@runtime_checkable
class DreamAdminOps(
    DreamGovernanceOps,
    DreamArchiveOps,
    DreamStatusOps,
    DreamDispatchOps,
    DreamReviewDecisionOps,
    Protocol,
):
    pass


# ── Dispatch boundary Protocols ──────────────────────────────────────
# Moved from engine/dream/dispatch/boundary.py — consumed by app layer.

from prismclaw.schema.dream.dream_types import DreamEnqueueResult


@runtime_checkable
class DreamEnqueueBoundary(Protocol):
    def is_available(self) -> bool: ...

    async def mark_activity(self, session_key: str, *, dirty_delta: int = 1) -> DreamEnqueueResult: ...

    async def enqueue_nightly(self, session_key: str) -> DreamEnqueueResult: ...


@runtime_checkable
class DreamBoundary(DreamEnqueueBoundary, Protocol):
    async def run_dispatched_job(self, job: SchedulerDispatchJob) -> bool: ...
