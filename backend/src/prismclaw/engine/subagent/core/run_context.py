from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from prismclaw.engine.execution.checkpoint.models import CommitState, ToolEffectRecord
from prismclaw.engine.execution.output.tool_result_rendering import (
    tool_result_to_llm_content,
)
from prismclaw.engine.subagent.core.persistence import SubagentPersistence
from prismclaw.engine.subagent.runtime.lease import SubagentLease, SubagentLeaseStore
from prismclaw.engine.toolkit.registry.tool_runtime import resolve_tool_effect_class
from prismclaw.schema.agent.capability import EffectClass
from prismclaw.schema.content.content_types import ToolResult
from prismclaw.schema.session.types import (
    SUBAGENT_TOOL_EFFECT_CUSTOM_TYPE,
    CustomEntry,
    MessageEntry,
    SessionTree,
    generate_entry_id,
)

if TYPE_CHECKING:
    from prismclaw.engine.toolkit.registry.registry import ToolRegistry


class SubagentRunInterruptedError(RuntimeError):
    """The current attempt must stop before another model/tool boundary."""


class SubagentCommitUnknownError(RuntimeError):
    """A tool result batch may or may not have been persisted."""


@dataclass(slots=True)
class SubagentRunContext:
    agent_id: str
    run_id: str
    persistence: SubagentPersistence
    lifecycle: Literal["one_shot", "persistent"]
    lease: SubagentLease | None = None
    lease_store: SubagentLeaseStore | None = None
    attempt: int = 1
    definition_fingerprint: str = ""
    interrupt_event: asyncio.Event = field(default_factory=asyncio.Event)
    control_probe: Callable[[], Awaitable[bool]] | None = None
    heartbeat_interval_s: float = 10.0
    recovery_blocked_reason: str | None = None
    _injected_message_ids: set[str] = field(default_factory=set, init=False)
    _pending_ack_ids: list[str] = field(default_factory=list, init=False)
    _effects: dict[str, dict[str, Any]] = field(default_factory=dict, init=False)
    _heartbeat_task: asyncio.Task[None] | None = field(default=None, init=False)
    _lease_lost: bool = field(default=False, init=False)

    @property
    def child_session_id(self) -> str:
        return self.persistence.child_session_id

    @property
    def child_tree(self) -> SessionTree:
        return self.persistence.tree

    @property
    def lease_lost(self) -> bool:
        return self._lease_lost

    def build_messages(self) -> list[dict[str, Any]]:
        pending = self.persistence.get_unacked_user_messages()
        message_ids = [self._message_id(entry) for entry in pending]
        self._injected_message_ids.update(message_ids)
        self._pending_ack_ids.extend(
            message_id for message_id in message_ids if message_id not in self._pending_ack_ids
        )
        return [dict(message) for message in self.persistence.rebuild_model_messages()]

    async def collect_pending_messages(self) -> list[dict[str, Any]]:
        await self.persistence.refresh()
        runtime_by_entry_id = {
            message.get("__entry_id__"): message
            for message in self.persistence.rebuild_model_messages()
        }
        collected: list[dict[str, Any]] = []
        for entry in self.persistence.get_unacked_user_messages():
            message_id = self._message_id(entry)
            if message_id in self._injected_message_ids:
                continue
            message = runtime_by_entry_id.get(entry.id)
            if message is None:
                continue
            self._injected_message_ids.add(message_id)
            if message_id not in self._pending_ack_ids:
                self._pending_ack_ids.append(message_id)
            collected.append(dict(message))
        return collected

    async def ensure_active(self) -> None:
        if not self.interrupt_event.is_set() and self.control_probe is not None:
            try:
                interrupted = await self.control_probe()
            except Exception:
                interrupted = True
            if interrupted:
                self.interrupt_event.set()
        if self.interrupt_event.is_set():
            raise SubagentRunInterruptedError("subagent interrupt requested")
        if self._lease_lost:
            raise SubagentRunInterruptedError("subagent lease lost")
        if self.lease is None or self.lease_store is None:
            return
        try:
            valid = await self.lease_store.validate(self.lease)
        except Exception:
            valid = False
        if not valid:
            self._lease_lost = True
            raise SubagentRunInterruptedError("subagent lease lost")

    async def persist_assistant(
        self,
        content: str,
        tool_calls: list[dict[str, Any]],
    ) -> None:
        ack_ids = list(self._pending_ack_ids)
        await self.persistence.append_assistant_message(
            content,
            agent_id=self.agent_id,
            run_id=self.run_id,
            attempt=self.attempt,
            ack_message_ids=ack_ids,
            tool_calls=tool_calls or None,
        )
        acknowledged = set(ack_ids)
        self._pending_ack_ids = [
            message_id for message_id in self._pending_ack_ids if message_id not in acknowledged
        ]

    async def before_tool_dispatch(
        self,
        tool_calls: list[dict[str, Any]],
        tool_registry: ToolRegistry,
    ) -> None:
        for call in tool_calls:
            await self.ensure_active()
            call_id = str(call.get("id") or "")
            function = call.get("function") or {}
            tool_name = str(function.get("name") or "unknown")
            tool = tool_registry.get(tool_name) if tool_name else None
            effect_class = (
                resolve_tool_effect_class(tool) if tool is not None else EffectClass.UNKNOWN_EFFECT
            )
            record = ToolEffectRecord(
                tool_call_id=call_id,
                tool_name=tool_name,
                effect_class=effect_class,
                idempotency_key=call_id or None,
                commit_state=CommitState.IN_PROGRESS,
                result_persisted=False,
                started_at_epoch_ms=_now_epoch_ms(),
            )
            payload = self._effect_payload(
                record,
                effect_id=f"fx:{uuid.uuid4()}",
                arguments_summary=_arguments_summary(function.get("arguments")),
            )
            self._effects[call_id] = payload
            await self.persistence.append_tool_effect(payload)
        await self.ensure_active()

    async def persist_tool_results(
        self,
        tool_calls: list[dict[str, Any]],
        results: list[ToolResult],
    ) -> None:
        entries: list[MessageEntry | CustomEntry] = []
        for call, result in zip(tool_calls, results, strict=False):
            call_id = str(call.get("id") or "")
            function = call.get("function") or {}
            tool_name = str(function.get("name") or "unknown")
            tool_call_id = call_id or result.tool_call_id
            entries.append(
                MessageEntry(
                    id=generate_entry_id(self.child_tree.all_entry_ids()),
                    parent_id=self.child_tree.leaf_id,
                    role="tool",
                    content=tool_result_to_llm_content(result),
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    is_error=result.is_error,
                    metadata={
                        "agent_id": self.agent_id,
                        "run_id": self.run_id,
                        "attempt": self.attempt,
                        "tool_call_id": tool_call_id,
                    },
                )
            )
            previous = self._effects.get(call_id)
            if previous is None:
                previous = self._effect_payload(
                    ToolEffectRecord(
                        tool_call_id=call_id,
                        tool_name=tool_name,
                        started_at_epoch_ms=None,
                    ),
                    effect_id=f"fx:{uuid.uuid4()}",
                    arguments_summary=_arguments_summary(function.get("arguments")),
                )
            record = ToolEffectRecord.model_validate(previous).model_copy(
                update={
                    "commit_state": CommitState.COMMITTED,
                    "result_persisted": True,
                    "finished_at_epoch_ms": _now_epoch_ms(),
                }
            )
            payload = dict(previous)
            payload.update(record.model_dump(mode="json"))
            payload["tool_is_error"] = result.is_error
            self._effects[call_id] = payload
            entries.append(self._tool_effect_entry(payload))

        try:
            await self.persistence.commit_entries(entries)
        except Exception as exc:
            unknown_entries: list[CustomEntry] = []
            for call in tool_calls:
                call_id = str(call.get("id") or "")
                previous = self._effects.get(call_id)
                if previous is None:
                    continue
                record = ToolEffectRecord.model_validate(previous).model_copy(
                    update={
                        "commit_state": CommitState.COMMIT_UNKNOWN,
                        "result_persisted": False,
                        "finished_at_epoch_ms": _now_epoch_ms(),
                    }
                )
                payload = dict(previous)
                payload.update(record.model_dump(mode="json"))
                self._effects[call_id] = payload
                unknown_entries.append(self._tool_effect_entry(payload))
            self.recovery_blocked_reason = "tool result commit state is unknown"
            with contextlib.suppress(Exception):
                await self.persistence.commit_entries(unknown_entries)
            raise SubagentCommitUnknownError(self.recovery_blocked_reason) from exc

    def start_heartbeat(self) -> None:
        if self.lease is None or self.lease_store is None or self._heartbeat_task is not None:
            return
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def stop_heartbeat(self) -> None:
        task = self._heartbeat_task
        self._heartbeat_task = None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _heartbeat_loop(self) -> None:
        assert self.lease is not None
        assert self.lease_store is not None
        while True:
            await asyncio.sleep(max(0.1, self.heartbeat_interval_s))
            try:
                valid = await self.lease_store.heartbeat(self.lease)
            except Exception:
                valid = False
            if not valid:
                self._lease_lost = True
                return

    def _effect_payload(
        self,
        record: ToolEffectRecord,
        *,
        effect_id: str,
        arguments_summary: str,
    ) -> dict[str, Any]:
        payload = record.model_dump(mode="json")
        payload.update(
            {
                "effect_id": effect_id,
                "agent_id": self.agent_id,
                "run_id": self.run_id,
                "attempt": self.attempt,
                "fence": self.lease.fence if self.lease is not None else 0,
                "arguments_summary": arguments_summary,
            }
        )
        return payload

    def _tool_effect_entry(self, payload: dict[str, Any]) -> CustomEntry:
        return CustomEntry(
            id=generate_entry_id(self.child_tree.all_entry_ids()),
            parent_id=self.child_tree.leaf_id,
            custom_type=SUBAGENT_TOOL_EFFECT_CUSTOM_TYPE,
            data=dict(payload),
        )

    @staticmethod
    def _message_id(entry: MessageEntry) -> str:
        metadata = entry.metadata or {}
        message_id = metadata.get("message_id")
        return message_id if isinstance(message_id, str) and message_id else entry.id


def _now_epoch_ms() -> int:
    return int(time.time() * 1000)


def _arguments_summary(raw_arguments: object) -> str:
    if isinstance(raw_arguments, str):
        text = raw_arguments
    else:
        try:
            text = json.dumps(raw_arguments or {}, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(raw_arguments)
    return text[:1000]
