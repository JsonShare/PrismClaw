from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from prismclaw.engine.security.audit.audit_types import ToolCallRecord, ApprovalRecord, ToolCallStatus

_FLUSH_INTERVAL_S = 5.0
_FLUSH_BATCH_SIZE = 10

logger = logging.getLogger(__name__)


class _SafetyUpdate:
    __slots__ = (
        "tool_call_id",
        "sandbox_tier",
        "confirmation_kind",
        "risk_assessment_json",
        "workspace_tag_flags",
        "consent_purposes",
    )

    def __init__(self, **kwargs: Any) -> None:
        for slot in self.__slots__:
            setattr(self, slot, kwargs.get(slot))


class _CompletionUpdate:
    __slots__ = (
        "tool_call_id",
        "status",
        "exit_code",
        "elapsed_ms",
        "error_summary",
        "completed_at",
    )

    def __init__(self, **kwargs: Any) -> None:
        for slot in self.__slots__:
            setattr(self, slot, kwargs.get(slot))


class AuditStore:
    def __init__(self, pool: Any) -> None:
        self._pool = pool
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._flush_task: asyncio.Task[None] | None = None
        self._degraded = False
        self._closed = False

    async def start(self) -> None:
        self._flush_task = asyncio.create_task(self._flush_worker())

    async def close(self) -> None:
        self._closed = True
        await self._drain()
        if self._flush_task is not None:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass

    def submit(self, record: ToolCallRecord | ApprovalRecord) -> None:
        if self._degraded:
            asyncio.ensure_future(self._sync_write(record))
        else:
            self._queue.put_nowait(record)

    def submit_tool_call_start(
        self,
        *,
        tool_call_id: str,
        session_id: str,
        turn_seq: int,
        iteration_seq: int,
        tool_name: str,
        args_hash: str,
        args_sanitized: str,
        args_preview: str,
        tenant_id: str | None = None,
    ) -> None:
        record = ToolCallRecord(
            id=tool_call_id,
            session_id=session_id,
            turn_seq=turn_seq,
            iteration_seq=iteration_seq,
            tool_name=tool_name,
            args_hash=args_hash,
            args_sanitized=args_sanitized,
            args_preview=args_preview,
            sandbox_tier="",
            confirmation_kind="",
            risk_assessment_json="",
            workspace_tag_flags="",
            consent_purposes=[],
            tenant_id=tenant_id,
        )
        self.submit(record)

    def submit_tool_call_safety(
        self,
        *,
        tool_call_id: str,
        sandbox_tier: str,
        confirmation_kind: str,
        risk_assessment_json: str,
        workspace_tag_flags: str,
        consent_purposes: list[str],
    ) -> None:
        self._queue.put_nowait(
            _SafetyUpdate(
                tool_call_id=tool_call_id,
                sandbox_tier=sandbox_tier,
                confirmation_kind=confirmation_kind,
                risk_assessment_json=risk_assessment_json,
                workspace_tag_flags=workspace_tag_flags,
                consent_purposes=consent_purposes,
            )
        )

    def submit_tool_call_complete(
        self,
        *,
        tool_call_id: str,
        status: ToolCallStatus,
        exit_code: int | None = None,
        elapsed_ms: int | None = None,
        error_summary: str | None = None,
    ) -> None:
        self._queue.put_nowait(
            _CompletionUpdate(
                tool_call_id=tool_call_id,
                status=status,
                exit_code=exit_code,
                elapsed_ms=elapsed_ms,
                error_summary=error_summary,
                completed_at=datetime.now(timezone.utc),
            )
        )

    def submit_approval(self, record: ApprovalRecord) -> None:
        self.submit(record)

    async def _flush_worker(self) -> None:
        while not self._closed:
            try:
                await asyncio.wait_for(self._queue.get(), timeout=_FLUSH_INTERVAL_S)
            except asyncio.TimeoutError:
                continue
            await self._flush_batch()

    async def _drain(self) -> None:
        while not self._queue.empty():
            await self._flush_batch()

    async def _flush_batch(self) -> None:
        batch: list[Any] = []
        while len(batch) < _FLUSH_BATCH_SIZE and not self._queue.empty():
            batch.append(self._queue.get_nowait())
        if not batch:
            return
        for attempt in range(3):
            try:
                async with self._pool.acquire() as conn:
                    for item in batch:
                        await self._write_one(conn, item)
                return
            except Exception:
                logger.exception("Flush batch attempt %d failed", attempt + 1)
        self._degraded = True
        logger.error("AuditStore degraded after 3 flush failures; falling back to sync writes")

    async def _write_one(self, conn: Any, item: Any) -> None:
        if isinstance(item, ToolCallRecord):
            await conn.execute(
                """
                INSERT INTO pc_tool_call_audit
                    (id, session_id, turn_seq, iteration_seq, tool_name,
                     args_hash, args_sanitized, args_preview, sandbox_tier,
                     confirmation_kind, risk_assessment_json, workspace_tag_flags,
                     consent_purposes, skill_name, plugin_name, subagent_run_id,
                     status, exit_code, elapsed_ms, error_summary,
                     created_at, completed_at, tenant_id)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23)
                """,
                item.id,
                item.session_id,
                item.turn_seq,
                item.iteration_seq,
                item.tool_name,
                item.args_hash,
                item.args_sanitized,
                item.args_preview,
                item.sandbox_tier,
                item.confirmation_kind,
                item.risk_assessment_json,
                item.workspace_tag_flags,
                json.dumps(item.consent_purposes),
                item.skill_name,
                item.plugin_name,
                item.subagent_run_id,
                item.status.value,
                item.exit_code,
                item.elapsed_ms,
                item.error_summary,
                item.created_at,
                item.completed_at,
                item.tenant_id,
            )
        elif isinstance(item, ApprovalRecord):
            await conn.execute(
                """
                INSERT INTO pc_approval_audit
                    (id, tool_call_id, decision, confirm_level,
                     requester_id, responder_id, second_approver_id,
                     signed_payload, verified, batch_size,
                     batch_tool_call_ids, created_at, resolved_at, tenant_id)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
                """,
                item.id,
                item.tool_call_id,
                item.decision.value,
                item.confirm_level,
                item.requester_id,
                item.responder_id,
                item.second_approver_id,
                item.signed_payload,
                item.verified,
                item.batch_size,
                json.dumps(item.batch_tool_call_ids),
                item.created_at,
                item.resolved_at,
                item.tenant_id,
            )
        elif isinstance(item, _SafetyUpdate):
            await conn.execute(
                """
                UPDATE pc_tool_call_audit
                SET sandbox_tier=$2,
                    confirmation_kind=$3,
                    risk_assessment_json=$4,
                    workspace_tag_flags=$5,
                    consent_purposes=$6
                WHERE id=$1
                """,
                item.tool_call_id,
                item.sandbox_tier,
                item.confirmation_kind,
                item.risk_assessment_json,
                item.workspace_tag_flags,
                json.dumps(item.consent_purposes),
            )
        elif isinstance(item, _CompletionUpdate):
            await conn.execute(
                """
                UPDATE pc_tool_call_audit
                SET status=$2,
                    exit_code=$3,
                    elapsed_ms=$4,
                    error_summary=$5,
                    completed_at=$6
                WHERE id=$1
                """,
                item.tool_call_id,
                item.status.value,
                item.exit_code,
                item.elapsed_ms,
                item.error_summary,
                item.completed_at,
            )

    async def _sync_write(self, record: Any) -> None:
        try:
            async with self._pool.acquire() as conn:
                await self._write_one(conn, record)
        except Exception:
            logger.exception("Sync write failed for audit record")
