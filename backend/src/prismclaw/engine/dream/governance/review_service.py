from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from prismclaw.schema.dream.dream import DreamDecisionAuditStore
from prismclaw.schema.dream.dream_types import DreamDecisionAuditRecord

from prismclaw.schema.dream.dream import DreamGovernanceStore
from prismclaw.schema.dream.dream_types import DreamGovernanceRecord
from prismclaw.schema.dream.dream import DreamStateStore
from prismclaw.engine.dream.engine import DreamEngine

if TYPE_CHECKING:
    from prismclaw.engine.dream.state.status_service import DreamStatusService
from prismclaw.schema.storage.storage_types import MemoryStore
from prismclaw.schema.storage.storage_types import SessionStore

def _empty_dispatch_status() -> dict[str, object]:
    return {
        "available": False,
        "stream_length": 0,
        "dead_letter_length": 0,
        "audit_length": 0,
        "oldest_dead_letter_at": None,
        "newest_dead_letter_at": None,
        "pending_count": 0,
        "pending_min_id": None,
        "pending_max_id": None,
        "consumer_count": 0,
        "consumers": [],
        "dedupe_ttl_seconds": 0,
        "max_retries": 0,
        "retry_base_delay_seconds": 0.0,
        "summary": {
            "health_status": "unavailable",
            "activity_state": "unavailable",
            "queue_backlog": 0,
            "blocked_replay_count": 0,
            "replayable_dead_letter_count": 0,
        },
        "dead_letter_metrics": {
            "total_count": 0,
            "sample_size": 0,
            "replayable_count": 0,
            "blocked_count": 0,
            "latest_error": None,
            "latest_session_key": None,
            "oldest_age_seconds": None,
            "newest_age_seconds": None,
        },
        "audit_metrics": {
            "sample_size": 0,
            "events_by_type": {},
            "events_by_kind": {},
            "latest_event": None,
            "latest_session_key": None,
        },
        "replay_metrics": {
            "candidate_count": 0,
            "replayable_count": 0,
            "blocked_count": 0,
            "blocked_reasons": {},
        },
        "session_health": {
            "impacted_session_count": 0,
            "blocked_session_count": 0,
            "items": [],
        },
    }

@dataclass(slots=True)
class DreamReviewService:
    dreaming_engine: DreamEngine
    status_service: DreamStatusService | None = None
    memory_store: MemoryStore | None = None
    session_store: SessionStore | None = None
    state_store: DreamStateStore | None = None
    task_manager: object | None = None
    decision_audit_store: DreamDecisionAuditStore | None = None
    dispatch_queue: object | None = None
    governance_store: DreamGovernanceStore | None = None

    async def list_pending_reviews(self, session_key: str | None = None) -> list[DreamGovernanceRecord]:
        return await self.dreaming_engine.list_pending_reviews(session_key)

    async def list_governance_records(
        self,
        *,
        session_key: str | None = None,
        review_status: str | None = None,
        publish_channel: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        limit: int = 100,
    ) -> list[DreamGovernanceRecord]:
        if self.governance_store is not None:
            return await self.governance_store.list_records(
                session_key=session_key,
                review_status=review_status,  # type: ignore[arg-type]
                publish_channel=publish_channel,  # type: ignore[arg-type]
                target_type=target_type,  # type: ignore[arg-type]
                target_id=target_id,
                limit=limit,
            )
        return await self.dreaming_engine.list_governance_records(
            session_key=session_key,
            review_status=review_status,  # type: ignore[arg-type]
            publish_channel=publish_channel,  # type: ignore[arg-type]
            target_type=target_type,  # type: ignore[arg-type]
            limit=limit,
        )

    async def get_governance_record(self, record_id: str) -> DreamGovernanceRecord | None:
        if self.governance_store is None:
            return None
        return await self.governance_store.get(record_id)

    async def get_governance_summary(self, session_key: str | None = None) -> dict[str, object]:
        records = await self.list_governance_records(session_key=session_key, limit=500)
        return {
            "total_records": len(records),
            "by_review_status": _count_by(records, key="review_status"),
            "by_publish_channel": _count_by(records, key="publish_channel"),
            "by_rollout_stage": _count_by(records, key="rollout_stage"),
            "by_target_type": _count_by(records, key="target_type"),
            "pending_review_count": sum(1 for record in records if record.review_status == "pending_review"),
            "shadow_pending_count": sum(
                1
                for record in records
                if record.publish_channel == "shadow" and record.review_status == "pending_review"
            ),
            "canary_pending_count": sum(
                1
                for record in records
                if record.publish_channel == "canary" and record.review_status == "pending_review"
            ),
            "memory_record_count": sum(1 for record in records if record.target_type == "memory"),
            "artifact_record_count": sum(1 for record in records if record.target_type == "artifact"),
        }

    async def approve(
        self,
        record_id: str,
        *,
        reviewer: str = "",
        comment: str = "",
        expected_target_id: str | None = None,
        expected_evidence_fingerprint: str | None = None,
    ) -> DreamGovernanceRecord | None:
        record = await self.get_governance_record(record_id)
        if record is None:
            approved = await self.dreaming_engine.approve_governance_record(record_id)
            if not approved:
                return None
            return DreamGovernanceRecord(
                record_id=record_id,
                session_key="",
                target_type="memory",
                target_id=record_id,
                publish_state="published",
                review_status="published",
            )
        if self.governance_store is None:
            return None
        if record.target_type == "compression":
            # Compression review was removed; legacy compression records are
            # not acted upon by the memory governance service.
            return None
        approved = await self.dreaming_engine.approve_governance_record(record_id)
        if not approved:
            return None
        return await self.get_governance_record(record_id)

    async def get_archive_storage(self, session_key: str) -> dict[str, int]:
        if self.memory_store is None:
            return {
                "archive_artifact_count": 0,
            }
        return await self.memory_store.get_archive_storage_stats(session_key)

    async def plan_canary_actions(
        self,
        *,
        session_key: str | None = None,
        limit: int = 100,
        trigger: str = "manual",
    ) -> dict[str, object]:
        records = await self.list_governance_records(
            session_key=session_key,
            review_status="pending_review",
            publish_channel="canary",
            limit=limit,
        )
        items: list[dict[str, object]] = []
        for record in records:
            snapshot = (
                await self.status_service.get_status(record.session_key)
                if self.status_service is not None
                else None
            )
            decision = _decide_canary_action(record, snapshot=snapshot, trigger=trigger)
            items.append(decision)
        return {
            "total_candidates": len(items),
            "approve_count": sum(1 for item in items if item["action"] == "approve"),
            "reject_count": sum(1 for item in items if item["action"] == "reject"),
            "hold_count": sum(1 for item in items if item["action"] == "hold"),
            "items": items,
        }

    async def reconcile_canary_actions(
        self,
        *,
        session_key: str | None = None,
        limit: int = 100,
        dry_run: bool = False,
        trigger: str = "manual",
    ) -> dict[str, object]:
        batch_id = f"canary:{int(time.time() * 1000)}"
        plan = await self.plan_canary_actions(session_key=session_key, limit=limit, trigger=trigger)
        items = list(plan.get("items", []))
        if dry_run:
            await self._record_canary_decisions(batch_id, items, dry_run=True, trigger=trigger)
            return {"status": "dry_run", "batch_id": batch_id, **plan}
        results: list[dict[str, object]] = []
        for item in items:
            action = str(item.get("action", "") or "")
            record_id = str(item.get("record_id", "") or "")
            if action == "approve":
                approved = await self.approve(record_id)
                results.append({**item, "applied": approved is not None})
            elif action == "reject":
                rejected = await self.reject(record_id, reason=str(item.get("reason", "") or ""))
                results.append({**item, "applied": rejected is not None})
            else:
                results.append({**item, "applied": False})
        await self._record_canary_decisions(batch_id, results, dry_run=False, trigger=trigger)
        return {
            "status": "reconciled",
            "batch_id": batch_id,
            "approve_count": sum(1 for item in results if item["action"] == "approve"),
            "reject_count": sum(1 for item in results if item["action"] == "reject"),
            "hold_count": sum(1 for item in results if item["action"] == "hold"),
            "applied_count": sum(1 for item in results if item.get("applied") is True),
            "items": results,
        }

    async def list_canary_decision_audits(
        self,
        *,
        session_key: str | None = None,
        batch_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, object]]:
        if self.decision_audit_store is None:
            return []
        entries = await self.decision_audit_store.list_entries(
            session_key=session_key,
            batch_id=batch_id,
            limit=limit,
        )
        return [
            {
                "decision_id": entry.decision_id,
                "batch_id": entry.batch_id,
                "session_key": entry.session_key,
                "record_id": entry.record_id,
                "target_id": entry.target_id,
                "action": entry.action,
                "reason": entry.reason,
                "health_status": entry.health_status,
                "rollout_percentage": entry.rollout_percentage,
                "trigger": entry.trigger,
                "dry_run": entry.dry_run,
                "applied": entry.applied,
                "created_at": entry.created_at,
            }
            for entry in entries
        ]

    async def list_statuses(self, session_key: str | None = None) -> list[dict[str, object]]:
        if self.status_service is None:
            return []
        return [snapshot.to_dict() for snapshot in await self.status_service.list_statuses(session_key)]

    async def get_dispatch_status(self) -> dict[str, object]:
        queue = self.dispatch_queue
        merged = _empty_dispatch_status()
        if queue is not None:
            metrics_fn = getattr(queue, "metrics", None)
            if metrics_fn is not None:
                merged.update(await metrics_fn())
            else:
                stats_fn = getattr(queue, "stats", None)
                if stats_fn is not None:
                    merged.update(await stats_fn())
        merged["session_health"] = await self._build_dispatch_session_health()
        return merged

    async def list_dispatch_dead_letters(
        self,
        *,
        session_key: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, object]]:
        queue = self.dispatch_queue
        if queue is None:
            return []
        list_fn = getattr(queue, "list_dead_letters", None)
        if list_fn is None:
            return []
        return await list_fn(session_key=session_key, limit=limit)

    async def list_dispatch_audit_events(
        self,
        *,
        session_key: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, object]]:
        queue = self.dispatch_queue
        if queue is None:
            return []
        list_fn = getattr(queue, "list_audit_events", None)
        if list_fn is None:
            return []
        return await list_fn(session_key=session_key, limit=limit)

    async def replay_dispatch_dead_letter(self, dead_letter_stream_id: str) -> dict[str, object]:
        queue = self.dispatch_queue
        if queue is None:
            return {"status": "unavailable"}
        replay_fn = getattr(queue, "replay_dead_letter", None)
        if replay_fn is None:
            return {"status": "unavailable"}
        return await replay_fn(dead_letter_stream_id)

    async def list_dispatch_replay_candidates(
        self,
        *,
        session_key: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, object]]:
        queue = self.dispatch_queue
        if queue is None:
            return []
        list_fn = getattr(queue, "list_replay_candidates", None)
        if list_fn is None:
            return []
        return await list_fn(session_key=session_key, limit=limit)

    async def replay_dispatch_dead_letters(
        self,
        *,
        session_key: str | None = None,
        limit: int = 50,
        dry_run: bool = False,
    ) -> dict[str, object]:
        queue = self.dispatch_queue
        if queue is None:
            return {"status": "unavailable"}
        replay_fn = getattr(queue, "replay_dead_letters", None)
        if replay_fn is None:
            return {"status": "unavailable"}
        return await replay_fn(session_key=session_key, limit=limit, dry_run=dry_run)

    async def _build_dispatch_session_health(self) -> dict[str, object]:
        if self.status_service is None:
            return {
                "impacted_session_count": 0,
                "blocked_session_count": 0,
                "items": [],
            }
        snapshots = await self.status_service.list_statuses()
        items: list[dict[str, object]] = []
        blocked_session_count = 0
        for snapshot in snapshots:
            blocked_count = int(snapshot.publications.dispatch_blocked_replay_count or 0)
            dead_letter_count = int(snapshot.publications.dispatch_dead_letter_count or 0)
            replayable_count = int(snapshot.publications.dispatch_replayable_dead_letter_count or 0)
            if dead_letter_count <= 0 and blocked_count <= 0 and replayable_count <= 0:
                continue
            if blocked_count > 0:
                blocked_session_count += 1
            items.append(
                {
                    "session_key": snapshot.session_key,
                    "state": snapshot.state,
                    "health_status": snapshot.quality.health_status,
                    "dispatch_dead_letter_count": dead_letter_count,
                    "dispatch_replayable_dead_letter_count": replayable_count,
                    "dispatch_blocked_replay_count": blocked_count,
                    "latest_dispatch_dead_letter_error": snapshot.quality.latest_dispatch_dead_letter_error,
                    "latest_dispatch_replay_block_reason": (
                        snapshot.quality.latest_dispatch_replay_block_reason
                    ),
                }
            )
        items.sort(
            key=lambda item: (
                -int(item.get("dispatch_blocked_replay_count", 0) or 0),
                -int(item.get("dispatch_dead_letter_count", 0) or 0),
                str(item.get("session_key", "") or ""),
            )
        )
        return {
            "impacted_session_count": len(items),
            "blocked_session_count": blocked_session_count,
            "items": items[:20],
        }

    async def reject(
        self,
        record_id: str,
        *,
        reviewer: str = "",
        comment: str = "",
        reason: str = "",
        expected_target_id: str | None = None,
        expected_evidence_fingerprint: str | None = None,
    ) -> DreamGovernanceRecord | None:
        record = await self.get_governance_record(record_id)
        if record is None:
            rejected = await self.dreaming_engine.reject_governance_record(record_id, reason=comment or reason)
            if not rejected:
                return None
            return DreamGovernanceRecord(
                record_id=record_id,
                session_key="",
                target_type="memory",
                target_id=record_id,
                publish_state="rejected",
                review_status="rejected",
            )
        if self.governance_store is None:
            return None
        effective_comment = comment or reason
        if record.target_type == "compression":
            # Compression review was removed; legacy compression records are
            # not acted upon by the memory governance service.
            return None
        rejected = await self.dreaming_engine.reject_governance_record(record_id, reason=effective_comment)
        if not rejected:
            return None
        return await self.get_governance_record(record_id)

    async def rollback_memory(self, session_key: str, memory_id: str) -> bool:
        return await self.dreaming_engine.rollback_memory_publication(session_key, memory_id)

    async def rollback_artifact(self, record_id: str, *, reason: str = "") -> bool:
        return await self.dreaming_engine.rollback_artifact_publication(record_id, reason=reason)

    async def request_artifact_rollback(self, record_id: str, *, reason: str = "") -> bool:
        return await self.dreaming_engine.request_artifact_rollback(record_id, reason=reason)

    async def _record_canary_decisions(
        self,
        batch_id: str,
        items: list[dict[str, object]],
        *,
        dry_run: bool,
        trigger: str,
    ) -> None:
        if self.decision_audit_store is None:
            return
        created_at = time.time()
        for index, item in enumerate(items):
            record_id = str(item.get("record_id", "") or "")
            decision_id = f"{batch_id}:{index}:{record_id}"
            await self.decision_audit_store.record(
                DreamDecisionAuditRecord(
                    decision_id=decision_id,
                    batch_id=batch_id,
                    session_key=str(item.get("session_key", "") or ""),
                    record_id=record_id,
                    target_id=str(item.get("target_id", "") or ""),
                    action=str(item.get("action", "") or "hold"),
                    reason=str(item.get("reason", "") or ""),
                    health_status=str(item.get("health_status", "") or "unknown"),
                    rollout_percentage=int(item.get("rollout_percentage", 0) or 0),
                    trigger=trigger,
                    dry_run=dry_run,
                    applied=bool(item.get("applied", False)),
                    created_at=created_at,
                )
            )

def _count_by(records: list[DreamGovernanceRecord], *, key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        label = str(getattr(record, key, "") or "").strip() or "unknown"
        counts[label] = counts.get(label, 0) + 1
    return counts

def _decide_canary_action(
    record: DreamGovernanceRecord,
    *,
    snapshot: object | None,
    trigger: str,
) -> dict[str, object]:
    metadata = dict(record.metadata)
    rollout_policy = metadata.get("rollout_policy") if isinstance(metadata, dict) else {}
    rollout_policy = rollout_policy if isinstance(rollout_policy, dict) else {}
    rollout_percentage = int(rollout_policy.get("percentage", 0) or 0)
    health_status = str(getattr(getattr(snapshot, "quality", None), "health_status", "") or "")
    dead_letter_count = int(
        getattr(getattr(snapshot, "publications", None), "dispatch_dead_letter_count", 0) or 0
    )
    latest_failure_reason = str(
        getattr(getattr(snapshot, "quality", None), "latest_failure_reason", "") or ""
    )
    latest_compile_status = str(
        getattr(getattr(snapshot, "quality", None), "latest_compile_status", "") or ""
    )
    if health_status == "degraded" or dead_letter_count > 0:
        return {
            "record_id": record.record_id,
            "session_key": record.session_key,
            "target_id": record.target_id,
            "action": "reject",
            "reason": latest_failure_reason or "auto_reject_degraded_canary",
            "health_status": health_status or "unknown",
            "rollout_percentage": rollout_percentage,
            "trigger": trigger,
        }
    if latest_compile_status == "failed":
        return {
            "record_id": record.record_id,
            "session_key": record.session_key,
            "target_id": record.target_id,
            "action": "hold",
            "reason": "awaiting_compile_recovery",
            "health_status": health_status or "unknown",
            "rollout_percentage": rollout_percentage,
            "trigger": trigger,
        }
    # SDD T2-4：legacy 归档迁移等待分支已随 pc_memory_archives 死表删除而移除
    # （backlog 恒 0，该 hold 条件不再存在）。
    if rollout_percentage >= 100 and health_status in {"healthy", "review_pending"}:
        return {
            "record_id": record.record_id,
            "session_key": record.session_key,
            "target_id": record.target_id,
            "action": "approve",
            "reason": "auto_promote_full_rollout",
            "health_status": health_status or "unknown",
            "rollout_percentage": rollout_percentage,
            "trigger": trigger,
        }
    return {
        "record_id": record.record_id,
        "session_key": record.session_key,
        "target_id": record.target_id,
        "action": "hold",
        "reason": "awaiting_rollout_or_manual_review",
        "health_status": health_status or "unknown",
        "rollout_percentage": rollout_percentage,
        "trigger": trigger,
    }
