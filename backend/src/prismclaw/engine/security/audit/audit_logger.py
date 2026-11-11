from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Literal

from prismclaw.schema.content.workspace import WorkspaceMode, GateApprovalDecision as ApprovalDecision

_AUDIT_LOGGER_NAME = "prismclaw.audit.decision"

DecidedBy = Literal[
    "auto:not-callable",
    "auto:callable",
    "auto:no-hook",
    "auto:timeout",
    "user",
]


class AuditLogger:
    """Emit structured JSON audit lines for approval decisions and tier changes.

    Tool-call audit is now handled by AuditStore (async PG persistence).
    This logger retains log_decision (for admin commands) and log_tier_change
    (for permission tier transitions).
    """

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger(_AUDIT_LOGGER_NAME)

    def log_decision(
        self,
        *,
        conv_id: str,
        session_id: str,
        channel: Literal["web"],
        tool_name: str,
        tool_call_id: str,
        tier: WorkspaceMode,
        decision: ApprovalDecision,
        decided_by: str,
        decided_at: datetime | None = None,
        elapsed_ms: int | None = None,
        user_visible_name: str | None = None,
        tier_source: str | None = None,
        forced_server: str | None = None,
        user_id: str | None = None,
        sandbox_backend: Literal["bwrap", "none"] | None = None,
        reason: str | None = None,
        approval_id: str | None = None,
        run_id: str | None = None,
        turn_id: str | None = None,
        approval_basis: str | None = None,
        risk_level: str | None = None,
        execution_started: bool | None = None,
        execution_result: str | None = None,
        subagent_name: str | None = None,
        subagent_source: str | None = None,
        parent_session_id: str | None = None,
        subagent_depth: int | None = None,
    ) -> None:
        """Emit one audit line. ``decided_by`` is one of :data:`DecidedBy` or
        a user identifier string (Web ``user_id``).

        Sprint 2 added ``tier_source``/``forced_server`` for the de-escalation
        path; Sprint 3 adds ``user_id``/``sandbox_backend`` for sandbox
        enforcement. All new fields are optional and omitted when None to
        preserve Sprint 1 grep-based consumer compatibility.
        """
        decided_at = decided_at or datetime.now(timezone.utc)
        payload = self._base_payload(
            event="approval_decision",
            ts=decided_at,
            conv_id=conv_id,
            session_id=session_id,
            channel=channel,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            tier=tier,
            decision=decision,
            decided_by=decided_by,
            approval_id=approval_id,
            run_id=run_id,
            turn_id=turn_id,
            approval_basis=approval_basis,
            risk_level=risk_level,
            tier_source=tier_source,
            forced_server=forced_server,
            user_id=user_id,
            sandbox_backend=sandbox_backend,
            reason=reason,
            execution_started=execution_started,
            execution_result=execution_result,
            subagent_name=subagent_name,
            subagent_source=subagent_source,
            parent_session_id=parent_session_id,
            subagent_depth=subagent_depth,
        )
        payload["decided_at"] = _iso(decided_at)
        if elapsed_ms is not None:
            payload["elapsed_ms"] = elapsed_ms
        if user_visible_name is not None:
            payload["user_visible_name"] = user_visible_name
        self._emit(payload)

    def log_tier_change(
        self,
        *,
        session_id: str,
        channel: Literal["web"],
        from_tier: WorkspaceMode | None,
        to_tier: WorkspaceMode,
        user_id: str | None = None,
        ts: datetime | None = None,
    ) -> None:
        """Emit a single line marking that a session switched permission tier.

        Lets operators answer "when did this session enter yolo?" with a single
        grep instead of inspecting every approval_decision line. Per the
        Kubernetes-audit precedent: structural bookmarks for tier changes
        complement (don't replace) per-action audit lines.
        """
        ts = ts or datetime.now(timezone.utc)
        payload: dict[str, object] = {
            "event": "permission_tier_changed",
            "ts": _iso(ts),
            "session_id": session_id,
            "channel": channel,
            "from_tier": from_tier,
            "to_tier": to_tier,
        }
        if user_id is not None:
            payload["user_id"] = user_id
        self._emit(payload)

    def _base_payload(
        self,
        *,
        event: str,
        ts: datetime,
        conv_id: str,
        session_id: str,
        channel: Literal["web"],
        tool_name: str,
        tool_call_id: str,
        tier: WorkspaceMode,
        decision: str,
        decided_by: str,
        approval_id: str | None = None,
        run_id: str | None = None,
        turn_id: str | None = None,
        approval_basis: str | None = None,
        risk_level: str | None = None,
        tier_source: str | None = None,
        forced_server: str | None = None,
        user_id: str | None = None,
        sandbox_backend: Literal["bwrap", "none"] | None = None,
        reason: str | None = None,
        execution_started: bool | None = None,
        execution_result: str | None = None,
        subagent_name: str | None = None,
        subagent_source: str | None = None,
        parent_session_id: str | None = None,
        subagent_depth: int | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "event": event,
            "ts": _iso(ts),
            "conv_id": conv_id,
            "session_id": session_id,
            "channel": channel,
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "tier": tier,
            "decision": decision,
            "decided_by": decided_by,
        }
        if approval_id is not None:
            payload["approval_id"] = approval_id
        if run_id is not None:
            payload["run_id"] = run_id
        if turn_id is not None:
            payload["turn_id"] = turn_id
        if approval_basis is not None:
            payload["approval_basis"] = approval_basis
        if risk_level is not None:
            payload["risk_level"] = risk_level
        if tier_source is not None:
            payload["tier_source"] = tier_source
        if forced_server is not None:
            payload["forced_server"] = forced_server
        if user_id is not None:
            payload["user_id"] = user_id
        if sandbox_backend is not None:
            payload["sandbox_backend"] = sandbox_backend
        if reason is not None:
            payload["reason"] = reason
        if execution_started is not None:
            payload["execution_started"] = execution_started
        if execution_result is not None:
            payload["execution_result"] = execution_result
        if subagent_name is not None:
            payload["subagent_name"] = subagent_name
        if subagent_source is not None:
            payload["subagent_source"] = subagent_source
        if parent_session_id is not None:
            payload["parent_session_id"] = parent_session_id
        if subagent_depth is not None:
            payload["subagent_depth"] = subagent_depth
        return payload

    def _emit(self, payload: dict[str, object]) -> None:
        self._logger.info(
            json.dumps(payload, separators=(",", ":"), sort_keys=False, default=str)
        )


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")
