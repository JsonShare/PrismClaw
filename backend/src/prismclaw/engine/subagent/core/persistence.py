from __future__ import annotations

import uuid
from typing import Any, Literal, Self

from prismclaw.engine.subagent.core.contract import (
    coerce_context_snapshot,
    render_task_envelope,
)
from prismclaw.schema.agent.data_types import ContentBlock
from prismclaw.schema.agent.session import AgentMessageDict
from prismclaw.schema.content.subagent import SubagentContextSnapshot
from prismclaw.schema.session.event_logger import SessionEventLogger
from prismclaw.schema.session.types import (
    SUBAGENT_TOOL_EFFECT_CUSTOM_TYPE,
    CustomEntry,
    MessageEntry,
    SessionTree,
    generate_entry_id,
)
from prismclaw.schema.storage.storage_types import SessionStore


class SubagentPersistence:
    """Persistence boundary for one child SessionTree."""

    def __init__(self, session_store: SessionStore, tree: SessionTree) -> None:
        self._session_store = session_store
        self.tree = tree
        self._event_logger = SessionEventLogger()

    @property
    def child_session_id(self) -> str:
        return self.tree.header.id

    @classmethod
    async def create(
        cls,
        session_store: SessionStore,
        *,
        parent_session_id: str,
        agent_id: str,
        workspace_id: str,
        task: str,
        context_mode: Literal["inherit", "isolated"] = "isolated",
        context_snapshot: SubagentContextSnapshot | dict[str, Any] | None = None,
        request_id: str | None = None,
        run_id: str | None = None,
        max_inherit_messages: int = 20,
        title: str | None = None,
    ) -> Self:
        parent_tree: SessionTree | None = None
        if context_mode == "inherit":
            parent_tree = await session_store.load(parent_session_id)
            if parent_tree is None:
                raise ValueError(f"parent session {parent_session_id!r} was not found")

        tree = await session_store.create_new_session(
            session_key=f"subagent:{parent_session_id}:{agent_id}",
            workspace_id=workspace_id,
            agent_id=agent_id,
            parent_session_id=parent_session_id,
            title=title,
        )
        persistence = cls(session_store, tree)
        entries = persistence._initial_messages(
            parent_tree=parent_tree,
            task=task,
            context_snapshot=context_snapshot,
            request_id=request_id,
            run_id=run_id,
            max_inherit_messages=max_inherit_messages,
        )
        if entries:
            await persistence._commit_message_entries(entries)
        return persistence

    @classmethod
    async def load(
        cls,
        session_store: SessionStore,
        child_session_id: str,
    ) -> Self | None:
        tree = await session_store.load(child_session_id)
        if tree is None:
            return None
        return cls(session_store, tree)

    def _initial_messages(
        self,
        *,
        parent_tree: SessionTree | None,
        task: str,
        context_snapshot: SubagentContextSnapshot | dict[str, Any] | None,
        request_id: str | None,
        run_id: str | None,
        max_inherit_messages: int,
    ) -> list[MessageEntry]:
        entries: list[MessageEntry] = []
        existing_ids = self.tree.all_entry_ids()
        if parent_tree is not None:
            inheritable = [
                entry
                for entry in parent_tree.get_branch()
                if isinstance(entry, MessageEntry) and entry.role in {"user", "assistant"}
            ]
            for source in inheritable[-max_inherit_messages:]:
                metadata = dict(source.metadata or {})
                metadata.update({"kind": "inherited", "source_entry_id": source.id})
                entry = source.model_copy(deep=True)
                # 父会话的 tool 结果不会一并继承；若保留 tool_calls，会生成
                # 「assistant 带 tool_calls 但缺少对应 tool 消息」的非法序列。
                entry = entry.model_copy(
                    update={"tool_calls": None, "tool_call_id": None}
                )
                entry.id = generate_entry_id(existing_ids)
                existing_ids.add(entry.id)
                entry.parent_id = entries[-1].id if entries else self.tree.leaf_id
                entry.seq = 0
                entry.metadata = metadata
                entries.append(entry)

        snapshot = (
            SubagentContextSnapshot(task_goal=task)
            if context_snapshot in (None, {})
            else coerce_context_snapshot(context_snapshot, fallback_task=task)
        )
        message_id = f"msg:{uuid.uuid4()}"
        metadata: dict[str, Any] = {
            "kind": "task",
            "message_id": message_id,
            "request_id": request_id or message_id,
            "source": "parent",
        }
        if run_id is not None:
            metadata["run_id"] = run_id
        entry = MessageEntry(
            id=generate_entry_id(existing_ids),
            parent_id=entries[-1].id if entries else self.tree.leaf_id,
            role="user",
            content=render_task_envelope(task, snapshot),
            metadata=metadata,
        )
        entries.append(entry)
        return entries

    async def refresh(self) -> None:
        tree = await self._session_store.load(self.child_session_id)
        if tree is None:
            raise ValueError(
                f"subagent child session {self.child_session_id!r} was not found"
            )
        self.tree = tree

    async def commit_entries(
        self,
        entries: list[MessageEntry | CustomEntry],
    ) -> list[MessageEntry | CustomEntry]:
        """Commit a linear entry batch and update the in-memory child projection."""
        events = []
        parent_id = self.tree.leaf_id
        for entry in entries:
            if entry.id in self.tree.entries:
                raise ValueError(f"entry id {entry.id} already exists")
            entry.parent_id = parent_id
            parent_id = entry.id
            if isinstance(entry, MessageEntry):
                events.extend(
                    self._event_logger.message_entry(self.child_session_id, entry, entry.id)
                )
            else:
                events.append(
                    self._event_logger.entry_updated(
                        self.child_session_id,
                        entry,
                        leaf_id=entry.id,
                    )
                )
        if not events:
            return entries
        committed = await self._session_store.commit_events(events)
        if len(committed) != len(entries):
            raise RuntimeError("subagent entry batch commit returned an incomplete projection")
        for entry, event in zip(entries, committed, strict=True):
            if isinstance(entry, MessageEntry):
                entry.parent_id = event.data.get("parent_id")
                entry.seq = event.seq
                self.tree.append(entry)
            else:
                entry_data = event.data["entry"]
                entry.parent_id = entry_data.get("parent_id")
                entry.seq = event.seq
                self.tree.entries[entry.id] = entry
                self.tree.leaf_id = entry.id
        return entries

    async def _commit_message_entries(
        self,
        entries: list[MessageEntry],
    ) -> list[MessageEntry]:
        await self.commit_entries(entries)
        return entries

    async def append_message(self, entry: MessageEntry) -> MessageEntry:
        await self._commit_message_entries([entry])
        return entry

    async def append_user_message(
        self,
        content: str | list[ContentBlock],
        *,
        message_id: str | None = None,
        request_id: str | None = None,
        run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        entry_id: str | None = None,
    ) -> MessageEntry:
        merged = dict(metadata or {})
        resolved_message_id = message_id or merged.get("message_id") or f"msg:{uuid.uuid4()}"
        merged.update(
            {
                "message_id": resolved_message_id,
                "request_id": request_id or merged.get("request_id") or resolved_message_id,
                "source": "parent",
            }
        )
        if run_id is not None:
            merged["run_id"] = run_id
        entry = MessageEntry(
            id=entry_id or generate_entry_id(self.tree.all_entry_ids()),
            parent_id=self.tree.leaf_id,
            role="user",
            content=content,
            metadata=merged,
        )
        return await self.append_message(entry)

    async def append_assistant_message(
        self,
        content: str | list[ContentBlock],
        *,
        agent_id: str,
        run_id: str,
        attempt: int,
        ack_message_ids: list[str] | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
        entry_id: str | None = None,
    ) -> MessageEntry:
        merged = dict(metadata or {})
        merged.update(
            {
                "agent_id": agent_id,
                "run_id": run_id,
                "attempt": attempt,
                "ack_message_ids": list(ack_message_ids or []),
            }
        )
        entry = MessageEntry(
            id=entry_id or generate_entry_id(self.tree.all_entry_ids()),
            parent_id=self.tree.leaf_id,
            role="assistant",
            content=content,
            tool_calls=tool_calls,
            metadata=merged,
        )
        return await self.append_message(entry)

    async def append_tool_message(
        self,
        content: str | list[ContentBlock],
        *,
        agent_id: str,
        run_id: str,
        attempt: int,
        tool_call_id: str,
        tool_name: str | None = None,
        is_error: bool = False,
        metadata: dict[str, Any] | None = None,
        entry_id: str | None = None,
    ) -> MessageEntry:
        merged = dict(metadata or {})
        merged.update(
            {
                "agent_id": agent_id,
                "run_id": run_id,
                "attempt": attempt,
                "tool_call_id": tool_call_id,
            }
        )
        entry = MessageEntry(
            id=entry_id or generate_entry_id(self.tree.all_entry_ids()),
            parent_id=self.tree.leaf_id,
            role="tool",
            content=content,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            is_error=is_error,
            metadata=merged,
        )
        return await self.append_message(entry)

    async def append_tool_effect(self, data: dict[str, Any]) -> CustomEntry:
        entry = CustomEntry(
            id=generate_entry_id(self.tree.all_entry_ids()),
            parent_id=self.tree.leaf_id,
            custom_type=SUBAGENT_TOOL_EFFECT_CUSTOM_TYPE,
            data=dict(data),
        )
        event = self._event_logger.entry_updated(
            self.child_session_id,
            entry,
            leaf_id=entry.id,
        )
        committed = await self._session_store.commit_events([event])
        entry_data = committed[0].data["entry"]
        entry.parent_id = entry_data.get("parent_id")
        entry.seq = committed[0].seq
        self.tree.entries[entry.id] = entry
        self.tree.leaf_id = entry.id
        return entry

    def rebuild_model_messages(self) -> list[AgentMessageDict]:
        return self.tree.build_runtime_messages()

    def get_unacked_user_messages(self) -> list[MessageEntry]:
        acked: set[str] = set()
        branch = self.tree.get_branch()
        for entry in branch:
            if not isinstance(entry, MessageEntry) or entry.role != "assistant":
                continue
            raw_ack_ids = (entry.metadata or {}).get("ack_message_ids")
            if isinstance(raw_ack_ids, list):
                acked.update(item for item in raw_ack_ids if isinstance(item, str))

        pending: list[MessageEntry] = []
        for entry in branch:
            if not isinstance(entry, MessageEntry) or entry.role != "user":
                continue
            metadata = entry.metadata or {}
            if metadata.get("kind") == "inherited":
                continue
            message_id = metadata.get("message_id")
            if not isinstance(message_id, str) or not message_id:
                message_id = entry.id
            if message_id not in acked:
                pending.append(entry)
        return pending

    def get_tool_effects(self) -> list[dict[str, Any]]:
        return [
            dict(entry.data)
            for entry in self.tree.get_branch()
            if isinstance(entry, CustomEntry)
            and entry.custom_type == SUBAGENT_TOOL_EFFECT_CUSTOM_TYPE
            and isinstance(entry.data, dict)
        ]
