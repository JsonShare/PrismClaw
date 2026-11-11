from __future__ import annotations

from typing import Any


class InProcessDreamAdminOps:
    def __init__(self, review_service: Any) -> None:
        self._review_service = review_service

    async def list_pending_reviews(self, session_key: str | None = None) -> list[Any]:
        return await self._review_service.list_pending_reviews(session_key)

    async def list_governance_records(
        self,
        *,
        session_key: str | None = None,
        review_status: str | None = None,
        publish_channel: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        limit: int = 100,
    ) -> list[Any]:
        return await self._review_service.list_governance_records(
            session_key=session_key,
            review_status=review_status,
            publish_channel=publish_channel,
            target_type=target_type,
            target_id=target_id,
            limit=limit,
        )

    async def get_governance_summary(self, *, session_key: str | None = None) -> dict[str, Any]:
        return await self._review_service.get_governance_summary(session_key=session_key)

    async def plan_canary_actions(
        self,
        *,
        session_key: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        return await self._review_service.plan_canary_actions(session_key=session_key, limit=limit)

    async def reconcile_canary_actions(
        self,
        *,
        session_key: str | None = None,
        limit: int = 100,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        return await self._review_service.reconcile_canary_actions(
            session_key=session_key,
            limit=limit,
            dry_run=dry_run,
        )

    async def list_canary_decision_audits(
        self,
        *,
        session_key: str | None = None,
        batch_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return await self._review_service.list_canary_decision_audits(
            session_key=session_key,
            batch_id=batch_id,
            limit=limit,
        )

    async def get_archive_storage(self, session_key: str) -> dict[str, Any]:
        return await self._review_service.get_archive_storage(session_key)

    async def list_statuses(self, session_key: str | None = None) -> list[dict[str, Any]]:
        return await self._review_service.list_statuses(session_key)

    async def get_dispatch_status(self) -> dict[str, Any]:
        return await self._review_service.get_dispatch_status()

    async def list_dispatch_dead_letters(
        self,
        *,
        session_key: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        return await self._review_service.list_dispatch_dead_letters(
            session_key=session_key,
            limit=limit,
        )

    async def list_dispatch_audit_events(
        self,
        *,
        session_key: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        return await self._review_service.list_dispatch_audit_events(
            session_key=session_key,
            limit=limit,
        )

    async def replay_dispatch_dead_letter(self, stream_id: str) -> dict[str, Any]:
        return await self._review_service.replay_dispatch_dead_letter(stream_id)

    async def list_dispatch_replay_candidates(
        self,
        *,
        session_key: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        return await self._review_service.list_dispatch_replay_candidates(
            session_key=session_key,
            limit=limit,
        )

    async def replay_dispatch_dead_letters(
        self,
        *,
        session_key: str | None = None,
        limit: int = 50,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        return await self._review_service.replay_dispatch_dead_letters(
            session_key=session_key,
            limit=limit,
            dry_run=dry_run,
        )

    async def approve(
        self,
        record_id: str,
        *,
        reviewer: str = "",
        comment: str = "",
        expected_target_id: str | None = None,
        expected_evidence_fingerprint: str | None = None,
    ) -> Any | None:
        return await self._review_service.approve(
            record_id,
            reviewer=reviewer,
            comment=comment,
            expected_target_id=expected_target_id,
            expected_evidence_fingerprint=expected_evidence_fingerprint,
        )

    async def reject(
        self,
        record_id: str,
        *,
        reviewer: str = "",
        comment: str = "",
        reason: str = "",
        expected_target_id: str | None = None,
        expected_evidence_fingerprint: str | None = None,
    ) -> Any | None:
        return await self._review_service.reject(
            record_id,
            reviewer=reviewer,
            comment=comment,
            reason=reason,
            expected_target_id=expected_target_id,
            expected_evidence_fingerprint=expected_evidence_fingerprint,
        )

    async def rollback_artifact(self, record_id: str, *, reason: str = "") -> bool:
        return await self._review_service.rollback_artifact(record_id, reason=reason)

    async def rollback_memory(self, session_key: str, memory_id: str) -> bool:
        return await self._review_service.rollback_memory(session_key, memory_id)
