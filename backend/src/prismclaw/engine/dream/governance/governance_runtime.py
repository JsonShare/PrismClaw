from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from prismclaw.schema.dream.dream import DreamGovernanceStore
from prismclaw.schema.dream.dream_types import DreamGovernanceRecord, DreamPublishChannel, build_governance_record_id
from prismclaw.engine.dream.models import (
    DreamArtifactPublishResult,
    DreamPublishResult,
    DreamPublishableArtifact,
    DreamPublishableMemory,
)
from prismclaw.engine.dream.publishing import (
    build_l4_artifact_entry_from_publishable,
    build_memory_entry_from_publishable,
    deserialize_publishable_artifact,
    deserialize_publishable_memory,
    serialize_publishable_artifact,
    serialize_publishable_memory,
)
from prismclaw.schema.storage.storage_types import MemoryStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _PublicationPlan:
    store_entry: bool
    stored_publish_state: str
    governance_publish_state: str
    review_status: str
    rollout_stage: str


class DreamGovernanceRuntime:
    def __init__(
        self,
        *,
        memory_store: MemoryStore,
        governance_store: DreamGovernanceStore | None,
        memory_publish_channel: DreamPublishChannel = "direct",
        artifact_publish_channel: DreamPublishChannel = "direct",
        canary_rollout_percentage: int = 0,
        canary_rollout_salt: str = "dream-canary",
    ) -> None:
        self._memory = memory_store
        self._governance_store = governance_store
        self._memory_publish_channel = memory_publish_channel
        self._artifact_publish_channel = artifact_publish_channel
        self._canary_rollout_percentage = max(0, min(100, int(canary_rollout_percentage)))
        self._canary_rollout_salt = str(canary_rollout_salt or "dream-canary")

    async def publish_memory(
        self,
        session_key: str,
        publishable: DreamPublishableMemory,
    ) -> DreamPublishResult:
        entry = build_memory_entry_from_publishable(publishable)
        plan = _resolve_publication_plan(self._memory_publish_channel)
        entry.publish_state = plan.stored_publish_state
        if plan.store_entry:
            await self._memory.store(session_key, entry)
        await self._record_governance_publication(
            session_key,
            target_type="memory",
            target_id=entry.id,
            publish_state=plan.governance_publish_state,
            review_status=plan.review_status,
            publish_channel=self._memory_publish_channel,
            rollout_stage=plan.rollout_stage,
            shadow=self._memory_publish_channel == "shadow",
            metadata={
                "memory_kind": publishable.memory_kind,
                "layer": publishable.layer,
                "source_refs": list(publishable.source_refs),
                "rollback_supported": True,
                "publish_channel": self._memory_publish_channel,
                "stored_publish_state": entry.publish_state,
                "rollout_policy": _build_rollout_policy_metadata(
                    publish_channel=self._memory_publish_channel,
                    rollout_percentage=self._canary_rollout_percentage,
                    rollout_salt=self._canary_rollout_salt,
                ),
                "publishable_payload": serialize_publishable_memory(publishable),
            },
        )
        return DreamPublishResult(
            memory_id=entry.id,
            publish_state=plan.governance_publish_state,
            memory_kind=publishable.memory_kind,
            layer=publishable.layer,
        )

    async def publish_artifact(
        self,
        session_key: str,
        artifact: DreamPublishableArtifact,
    ) -> DreamArtifactPublishResult:
        artifact_entry = build_l4_artifact_entry_from_publishable(artifact)
        plan = _resolve_publication_plan(self._artifact_publish_channel)
        artifact_entry.publish_state = plan.stored_publish_state
        if plan.store_entry:
            await self._memory.store_l4_artifact(session_key, artifact_entry)
        await self._record_governance_publication(
            session_key,
            target_type="artifact",
            target_id=artifact_entry.id,
            publish_state=plan.governance_publish_state,
            review_status=plan.review_status,
            publish_channel=self._artifact_publish_channel,
            rollout_stage=plan.rollout_stage,
            shadow=self._artifact_publish_channel == "shadow",
            metadata={
                "artifact_kind": artifact.artifact_kind,
                "artifact_id": artifact_entry.id,
                "artifact_session_id": artifact.session_id,
                "graph_group_id": artifact.graph_group_id,
                "source_refs": list(artifact.source_refs),
                "rollback_supported": True,
                "publish_channel": self._artifact_publish_channel,
                "stored_publish_state": artifact_entry.publish_state,
                "rollout_policy": _build_rollout_policy_metadata(
                    publish_channel=self._artifact_publish_channel,
                    rollout_percentage=self._canary_rollout_percentage,
                    rollout_salt=self._canary_rollout_salt,
                ),
                "publishable_payload": serialize_publishable_artifact(artifact),
            },
        )
        return DreamArtifactPublishResult(
            artifact_id=artifact_entry.id,
            artifact_kind=artifact.artifact_kind,
            publish_state=plan.governance_publish_state,
        )

    async def approve_governance_record(self, record_id: str) -> bool:
        if self._governance_store is None:
            return False
        record = await self._governance_store.get(record_id)
        if record is None or record.review_status != "pending_review":
            return False
        if record.target_type == "memory":
            payload = deserialize_publishable_memory(record.metadata.get("publishable_payload"))
            if payload is None:
                return False
            entry = build_memory_entry_from_publishable(payload)
            entry.publish_state = "published"
            await self._memory.store(record.session_key, entry)
        elif record.target_type == "artifact":
            payload = deserialize_publishable_artifact(record.metadata.get("publishable_payload"))
            if payload is None:
                return False
            artifact_entry = build_l4_artifact_entry_from_publishable(payload)
            artifact_entry.publish_state = "published"
            await self._memory.store_l4_artifact(
                record.session_key,
                artifact_entry,
            )
        elif record.target_type == "compression":
            logger.warning(
                "compression governance record %s must be approved via DreamReviewService",
                record_id,
            )
            return False
        else:
            return False
        updated = await self._governance_store.update_status(
            record_id,
            publish_state="published",
            review_status="published",
            metadata_updates={
                "approved_at": time.time(),
                "approval_action": "promote_live",
                "shadow_promoted": record.publish_channel == "shadow",
                "canary_promoted": record.publish_channel == "canary",
            },
        )
        if updated is not None:
            updated = await self._governance_store.update_status(
                record_id,
                metadata_updates={"rollout_transition_at": time.time()},
            )
        if updated is not None:
            updated.rollout_stage = "live"
            await self._governance_store.record(updated)
        return updated is not None

    async def reject_governance_record(self, record_id: str, *, reason: str = "") -> bool:
        if self._governance_store is None:
            return False
        record = await self._governance_store.get(record_id)
        if record is None:
            return False
        if record.target_type == "compression":
            logger.warning(
                "compression governance record %s must be rejected via DreamReviewService",
                record_id,
            )
            return False
        if record.publish_channel == "canary":
            if record.target_type == "memory":
                try:
                    await self._memory.delete(record.session_key, record.target_id)
                except Exception:
                    logger.warning("canary memory rejection cleanup failed for %s", record.target_id, exc_info=True)
            elif record.target_type == "artifact":
                try:
                    await self._memory.delete_l4_artifact(record.session_key, record.target_id)
                except Exception:
                    logger.warning("canary artifact rejection cleanup failed for %s", record.target_id, exc_info=True)
        updated = await self._governance_store.update_status(
            record_id,
            publish_state="rejected",
            review_status="rejected",
            metadata_updates={
                "rejected_at": time.time(),
                "rejection_reason": reason,
                "approval_action": "reject",
            },
        )
        if updated is not None:
            updated.rollout_stage = "rejected"
            await self._governance_store.record(updated)
        return updated is not None

    async def rollback_memory_publication(self, session_key: str, memory_id: str) -> bool:
        try:
            await self._memory.delete(session_key, memory_id)
        except Exception:
            logger.warning("memory rollback failed for %s", memory_id, exc_info=True)
            return False
        await self._update_governance_after_rollback(
            session_key,
            target_type="memory",
            target_id=memory_id,
        )
        return True

    async def rollback_artifact_publication(self, record_id: str, *, reason: str = "") -> bool:
        if self._governance_store is None:
            return False
        record = await self._governance_store.get(record_id)
        if record is None or record.target_type != "artifact":
            return False
        if record.review_status == "rolled_back":
            return False
        if record.review_status not in {"published", "rollback_requested"} and record.publish_state != "published":
            return False
        try:
            deleted = await self._memory.delete_l4_artifact(record.session_key, record.target_id)
        except Exception:
            logger.warning("artifact rollback failed for %s", record.target_id, exc_info=True)
            return False
        if not deleted:
            return False
        updated = await self._governance_store.update_status(
            record_id,
            review_status="rolled_back",
            metadata_updates={
                "artifact_rolled_back_at": time.time(),
                "artifact_rollback_reason": reason,
            },
        )
        if updated is not None:
            updated.rollout_stage = "rolled_back"
            await self._governance_store.record(updated)
        return updated is not None

    async def request_artifact_rollback(self, record_id: str, *, reason: str = "") -> bool:
        if self._governance_store is None:
            return False
        record = await self._governance_store.get(record_id)
        if record is None or record.target_type != "artifact":
            return False
        updated = await self._governance_store.update_status(
            record_id,
            review_status="rollback_requested",
            metadata_updates={
                "artifact_rollback_requested_at": time.time(),
                "artifact_rollback_reason": reason,
            },
        )
        if updated is not None:
            updated.rollout_stage = "rollback_requested"
            await self._governance_store.record(updated)
        return updated is not None

    async def list_pending_reviews(self, session_key: str | None = None) -> list[DreamGovernanceRecord]:
        if self._governance_store is None:
            return []
        return await self._governance_store.list_records(
            session_key=session_key,
            review_status="pending_review",
            limit=200,
        )

    async def _record_governance_publication(
        self,
        session_key: str,
        *,
        target_type: str,
        target_id: str,
        publish_state: str,
        review_status: str,
        publish_channel: DreamPublishChannel,
        rollout_stage: str,
        shadow: bool,
        metadata: dict[str, Any],
    ) -> None:
        if self._governance_store is None:
            return
        await self._governance_store.record(
            record=DreamGovernanceRecord(
                record_id=build_governance_record_id(),
                session_key=session_key,
                target_type=target_type,  # type: ignore[arg-type]
                target_id=target_id,
                publish_state=publish_state,  # type: ignore[arg-type]
                review_status=review_status,  # type: ignore[arg-type]
                publish_channel=publish_channel,
                rollout_stage=rollout_stage,  # type: ignore[arg-type]
                shadow=shadow,
                metadata=metadata,
            )
        )

    async def _update_governance_after_rollback(
        self,
        session_key: str,
        *,
        target_type: str,
        target_id: str,
    ) -> None:
        if self._governance_store is None:
            return
        records = await self._governance_store.list_records(
            session_key=session_key,
            target_type=target_type,  # type: ignore[arg-type]
            limit=100,
        )
        for record in records:
            if record.target_id != target_id:
                continue
            await self._governance_store.update_status(
                record.record_id,
                review_status="rolled_back",
                metadata_updates={"rolled_back_at": time.time()},
            )
            updated = await self._governance_store.get(record.record_id)
            if updated is None:
                continue
            updated.rollout_stage = "rolled_back"
            await self._governance_store.record(updated)

def _resolve_publication_plan(channel: DreamPublishChannel) -> _PublicationPlan:
    if channel == "shadow":
        return _PublicationPlan(
            store_entry=False,
            stored_publish_state="reviewed",
            governance_publish_state="reviewed",
            review_status="pending_review",
            rollout_stage="shadow",
        )
    if channel == "canary":
        return _PublicationPlan(
            store_entry=True,
            stored_publish_state="canary",
            governance_publish_state="canary",
            review_status="pending_review",
            rollout_stage="canary",
        )
    return _PublicationPlan(
        store_entry=True,
        stored_publish_state="published",
        governance_publish_state="published",
        review_status="published",
        rollout_stage="live",
    )


def _build_rollout_policy_metadata(
    *,
    publish_channel: DreamPublishChannel,
    rollout_percentage: int,
    rollout_salt: str,
) -> dict[str, Any]:
    if publish_channel != "canary":
        return {"mode": "none", "percentage": 0}
    return {
        "mode": "hash_percent",
        "percentage": max(0, min(100, int(rollout_percentage))),
        "salt": str(rollout_salt or "dream-canary"),
    }
