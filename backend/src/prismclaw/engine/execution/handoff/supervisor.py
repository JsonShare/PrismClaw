from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from prismclaw.engine.execution.handoff.match import (
    find_merge_matches,
    find_single_match,
    order_merged_chains,
)
from prismclaw.engine.execution.handoff.contract import (
    HandoffRequestEntry,
    handoff_request_entry_from_payload,
)
from prismclaw.engine.execution.handoff.store import PendingHandoffStore
from prismclaw.schema.content.content_types import ToolResult

logger = logging.getLogger(__name__)
_AUDIT_LOGGER_NAME = "prismclaw.audit.subagent"
_SUB_AGENT_TOOL_NAME = "sub_agent"


@dataclass
class HandoffDispatchExtras:
    handoff_chain_id: str
    handoff_hop_index: int
    effective_hop_index: int
    from_subagent: str | None = None
    source_skill_origin: str | None = None
    source_preload_skills: list[str] | None = None
    source_is_fork: bool = False
    merged_from_chains: list[str] | None = None


@dataclass
class CapExceededInfo:
    handoff_chain_id: str
    requested_target: str
    requested_task_preview: str
    source_subagent: str
    effective_hop_count: int
    cap: int
    source_skill_origin: str | None = None
    source_preload_skills: list[str] | None = None
    source_is_fork: bool = False


class HandoffSupervisor:
    def __init__(
        self,
        store: PendingHandoffStore,
        *,
        audit_logger: logging.Logger | None = None,
        max_handoff_chain_length: int = 5,
    ) -> None:
        self._store = store
        self._audit = audit_logger or logging.getLogger(_AUDIT_LOGGER_NAME)
        self._turn_counter: dict[str, int] = {}
        self._max_chain_length = max_handoff_chain_length

    @property
    def max_chain_length(self) -> int:
        return self._max_chain_length

    @property
    def store(self) -> PendingHandoffStore:
        return self._store

    def increment_turn(self, session_id: str) -> int:
        self._turn_counter[session_id] = self._turn_counter.get(session_id, 0) + 1
        return self._turn_counter[session_id]

    def current_turn(self, session_id: str) -> int:
        return self._turn_counter.get(session_id, 0)

    async def insert_from_tool_results(
        self, *, session_id: str, results: list[ToolResult]
    ) -> int:
        n_inserted = 0
        for result in results:
            artifact = result.persistence_artifact
            if artifact is None or artifact.handoff_request is None:
                continue
            entry = self._build_entry(
                artifact.handoff_request.model_dump(mode="python", exclude_none=True)
            )
            if entry is None:
                continue
            current_turn = self.current_turn(session_id)
            await self._store.insert(
                session_id=session_id, entry=entry, current_turn=current_turn
            )
            self._emit_chain_started(
                session_id=session_id,
                entry=entry,
            )
            n_inserted += 1
        return n_inserted

    async def resolve_dispatch_extras(
        self,
        *,
        session_id: str,
        sub_agent_calls: list[dict[str, Any]],
    ) -> tuple[dict[str, HandoffDispatchExtras], dict[str, CapExceededInfo]]:
        if not sub_agent_calls:
            return {}, {}
        entries = await self._store.get_all(session_id)
        if not entries:
            return {}, {}

        result_map: dict[str, HandoffDispatchExtras] = {}
        cap_exceeded_map: dict[str, CapExceededInfo] = {}
        for call in sub_agent_calls:
            call_id = call.get("id", "")
            if not call_id:
                continue
            target, task = _extract_target_task(call)
            if not target or not task:
                continue
            merge_matches = find_merge_matches(
                entries, target=target, task=task
            )
            if len(merge_matches) >= 2:
                source_entries = [m.entry for m in merge_matches]
                ordered = order_merged_chains(source_entries)
                merged_chain_id = uuid.uuid4().hex
                effective = (
                    max(e.source_effective_hop_index for e in ordered) + 1
                )
                for entry in source_entries:
                    await self._store.consume(
                        session_id, _entry_to_subkey(entry)
                    )
                entries = await self._store.get_all(session_id)
                if effective >= self._max_chain_length:
                    cap_info = CapExceededInfo(
                        handoff_chain_id=merged_chain_id,
                        requested_target=target,
                        requested_task_preview=task[:200],
                        source_subagent=ordered[0].source_subagent,
                        source_skill_origin=ordered[0].source_skill_origin,
                        source_preload_skills=list(ordered[0].source_preload_skills),
                        source_is_fork=ordered[0].source_is_fork,
                        effective_hop_count=effective,
                        cap=self._max_chain_length,
                    )
                    cap_exceeded_map[call_id] = cap_info
                    self._emit_chain_terminated(
                        session_id=session_id,
                        cap_info=cap_info,
                        terminated_at_hop=0,
                    )
                else:
                    result_map[call_id] = HandoffDispatchExtras(
                        handoff_chain_id=merged_chain_id,
                        handoff_hop_index=0,
                        effective_hop_index=effective,
                        from_subagent=ordered[0].source_subagent,
                        source_skill_origin=ordered[0].source_skill_origin,
                        source_preload_skills=list(ordered[0].source_preload_skills),
                        source_is_fork=ordered[0].source_is_fork,
                        merged_from_chains=[e.source_chain_id for e in ordered],
                    )
                continue

            single = find_single_match(entries, target=target, task=task)
            if single is not None:
                effective = single.entry.source_effective_hop_index + 1
                if effective >= self._max_chain_length:
                    consumed = await self._store.consume(
                        session_id, _entry_to_subkey(single.entry)
                    )
                    if consumed:
                        entries = await self._store.get_all(session_id)
                    cap_info = CapExceededInfo(
                        handoff_chain_id=single.entry.source_chain_id,
                        requested_target=target,
                        requested_task_preview=task[:200],
                        source_subagent=single.entry.source_subagent,
                        source_skill_origin=single.entry.source_skill_origin,
                        source_preload_skills=list(single.entry.source_preload_skills),
                        source_is_fork=single.entry.source_is_fork,
                        effective_hop_count=effective,
                        cap=self._max_chain_length,
                    )
                    cap_exceeded_map[call_id] = cap_info
                    self._emit_chain_terminated(
                        session_id=session_id,
                        cap_info=cap_info,
                        terminated_at_hop=single.entry.source_hop_index + 1,
                    )
                else:
                    consumed = await self._store.consume(
                        session_id, _entry_to_subkey(single.entry)
                    )
                    if consumed:
                        entries = await self._store.get_all(session_id)
                        result_map[call_id] = HandoffDispatchExtras(
                            handoff_chain_id=single.entry.source_chain_id,
                            handoff_hop_index=single.entry.source_hop_index + 1,
                            effective_hop_index=effective,
                            from_subagent=single.entry.source_subagent,
                            source_skill_origin=single.entry.source_skill_origin,
                            source_preload_skills=list(single.entry.source_preload_skills),
                            source_is_fork=single.entry.source_is_fork,
                        )
        return result_map, cap_exceeded_map

    def build_cap_exceeded_error_text(self, info: CapExceededInfo, *, terminated_at_hop: int) -> str:
        return (
            f"[移交链长度已达上限：本次将是第 "
            f"{terminated_at_hop} 跳（有效跳数 {info.effective_hop_count}），"
            f"上限为 {info.cap}。链历史已在你的消息上下文中。"
            f"请直接决定如何作答，不要再发起移交。]"
        )

    def _emit_chain_terminated(
        self,
        *,
        session_id: str,
        cap_info: CapExceededInfo,
        terminated_at_hop: int,
    ) -> None:
        payload = {
            "event": "subagent_handoff_chain_terminated",
            "ts": _now_iso(),
            "handoff_chain_id": cap_info.handoff_chain_id,
            "terminated_at_hop": terminated_at_hop,
            "effective_hop_count": cap_info.effective_hop_count,
            "requested_target": cap_info.requested_target,
            "requested_task_preview": cap_info.requested_task_preview,
            "cap": cap_info.cap,
            "session_id": session_id,
        }
        if cap_info.source_skill_origin is not None:
            payload["source_skill_origin"] = cap_info.source_skill_origin
        if cap_info.source_preload_skills:
            payload["source_preload_skills"] = list(cap_info.source_preload_skills)
        if cap_info.source_is_fork:
            payload["source_is_fork"] = True
        self._emit_audit(payload)

    async def expire_aged(
        self, *, session_id: str, ttl_turns: int
    ) -> list[HandoffRequestEntry]:
        current_turn = self.current_turn(session_id)
        expired = await self._store.expire_aged(
            session_id, current_turn=current_turn, ttl_turns=ttl_turns
        )
        for entry in expired:
            self._emit_request_expired(
                session_id=session_id, entry=entry, ttl_turns=ttl_turns
            )
        return expired

    async def clear_session(self, session_id: str) -> int:
        self._turn_counter.pop(session_id, None)
        return await self._store.clear_session(session_id)

    def _build_entry(
        self, handoff_request: dict[str, Any]
    ) -> HandoffRequestEntry | None:
        entry = handoff_request_entry_from_payload(handoff_request)
        if entry is None:
            logger.warning(
                "handoff_supervisor.build_entry.missing_field handoff_request_keys=%s",
                sorted(handoff_request.keys()),
            )
        return entry

    def _emit_chain_started(
        self,
        *,
        session_id: str,
        entry: HandoffRequestEntry,
    ) -> None:
        payload = {
            "event": "handoff_chain_started",
            "ts": _now_iso(),
            "handoff_chain_id": entry.source_chain_id,
            "origin_subagent_name": entry.source_subagent,
            "origin_sub_session_id": entry.source_sub_session_id,
            "origin_target": entry.target,
            "session_id": session_id,
        }
        if entry.source_skill_origin is not None:
            payload["origin_skill_origin"] = entry.source_skill_origin
        if entry.source_preload_skills:
            payload["origin_preload_skills"] = list(entry.source_preload_skills)
        if entry.source_is_fork:
            payload["origin_is_fork"] = True
        self._emit_audit(payload)

    def _emit_request_expired(
        self,
        *,
        session_id: str,
        entry: HandoffRequestEntry,
        ttl_turns: int,
    ) -> None:
        payload = {
            "event": "handoff_request_expired",
            "ts": _now_iso(),
            "handoff_chain_id": entry.source_chain_id,
            "source_subagent_name": entry.source_subagent,
            "source_sub_session_id": entry.source_sub_session_id,
            "target": entry.target,
            "expired_after_turns": ttl_turns,
            "session_id": session_id,
        }
        if entry.source_skill_origin is not None:
            payload["source_skill_origin"] = entry.source_skill_origin
        if entry.source_preload_skills:
            payload["source_preload_skills"] = list(entry.source_preload_skills)
        if entry.source_is_fork:
            payload["source_is_fork"] = True
        self._emit_audit(payload)

    def _emit_audit(self, payload: dict[str, Any]) -> None:
        try:
            self._audit.info(
                json.dumps(payload, separators=(",", ":"), sort_keys=False)
            )
        except Exception:
            logger.warning(
                "handoff_supervisor.audit.emit_failed event=%s",
                payload.get("event"),
                exc_info=True,
            )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _entry_to_subkey(entry: HandoffRequestEntry) -> str:
    from prismclaw.engine.execution.handoff.store import composite_subkey

    return composite_subkey(
        source_sub_session_id=entry.source_sub_session_id,
        target=entry.target,
        task=entry.task,
    )


def _extract_target_task(call: dict[str, Any]) -> tuple[str, str]:
    fn = call.get("function") or {}
    if (fn.get("name") or "") != _SUB_AGENT_TOOL_NAME:
        return "", ""
    raw_args = fn.get("arguments")
    if isinstance(raw_args, str):
        try:
            args = json.loads(raw_args)
        except (json.JSONDecodeError, TypeError):
            return "", ""
    elif isinstance(raw_args, dict):
        args = raw_args
    else:
        return "", ""
    target = args.get("agent_name") or ""
    task = args.get("task") or ""
    return target, task


__all__ = ["CapExceededInfo", "HandoffDispatchExtras", "HandoffSupervisor"]
