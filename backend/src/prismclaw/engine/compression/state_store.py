from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from prismclaw.engine.state.state_protocol import (
    _RECENT_MESSAGE_LIMIT,
    diff_state_payload,
    extract_message_text,
    extract_state_update,
    render_state_payload_block,
    session_memory_payload_from_state,
    state_payload_from_state,
)
from prismclaw.schema.content.context import ContextBlock
from prismclaw.schema.session.types import SessionTree
from prismclaw.schema.agent.session import SessionContextState


def _snippet(value: str, *, limit: int = 240) -> str:
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


@dataclass
class SessionStateStore:
    _states: dict[str, SessionContextState] = field(default_factory=dict)

    def get_existing_state(self, session_id: str) -> SessionContextState | None:
        return self._states.get(session_id)

    def get_state(self, session_id: str) -> SessionContextState:
        return self._states.setdefault(session_id, SessionContextState(session_id=session_id))

    def hydrate_state(self, session_id: str, state: SessionContextState) -> SessionContextState:
        copied = state.model_copy(deep=True)
        self._states[session_id] = copied
        return copied

    def ingest_message(self, session_id: str, message: dict[str, Any]) -> SessionContextState:
        state = self.get_state(session_id)
        role = str(message.get("role") or "unknown")
        text = extract_message_text(message)
        if text.strip():
            state.record_recent_message(role, _snippet(text))
        state.apply_update(extract_state_update([message]))
        return state

    def rebuild_from_messages(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
    ) -> SessionContextState:
        state = SessionContextState(session_id=session_id)
        for message in messages[-_RECENT_MESSAGE_LIMIT:]:
            role = str(message.get("role") or "unknown")
            text = extract_message_text(message)
            if text.strip():
                state.record_recent_message(role, _snippet(text))
        state.apply_update(extract_state_update(messages))
        self._states[session_id] = state
        return state

    def build_memory_block(self, session_id: str) -> ContextBlock | None:
        state = self.get_state(session_id)
        payload = session_memory_payload_from_state(state)
        content = render_state_payload_block(wrapper="session_memory", state_payload=payload)
        if not content:
            return None
        return ContextBlock(
            id="session_memory",
            zone="dynamic",
            source="session_memory",
            content=content,
            stability="semi_stable",
            priority="critical",
            cacheable=False,
            truncatable=False,
            metadata={"wrapper": "session_memory"},
            state_payload=payload,
        )

    def build_persisted_memory_block(
        self,
        session_id: str,
        *,
        baseline_state: SessionContextState | None,
    ) -> ContextBlock | None:
        """Run 级持久记忆锚块——检索/profile 的输入，基于持久基线而非滚动状态。

        滚动 state 每消息更新（record_recent_message + apply_update），
        若用它渲染会污染 Run 级缓存键与检索 query（Step 2 归位）。
        基线缺失时回退 ``build_memory_block``（行为与重构前一致）。
        """
        if baseline_state is None or not baseline_state.has_content():
            return self.build_memory_block(session_id)
        payload = session_memory_payload_from_state(baseline_state)
        content = render_state_payload_block(wrapper="session_memory", state_payload=payload)
        if not content:
            return None
        return ContextBlock(
            id="session_memory",
            zone="dynamic",
            source="session_memory",
            content=content,
            stability="semi_stable",
            priority="critical",
            cacheable=False,
            truncatable=False,
            metadata={"wrapper": "session_memory"},
            state_payload=payload,
        )

    def build_context_block(
        self,
        session_id: str,
        *,
        baseline_state: SessionContextState | None = None,
    ) -> ContextBlock | None:
        state = self.get_state(session_id)
        payload = state_payload_from_state(state)
        if baseline_state is not None:
            payload = diff_state_payload(payload, session_memory_payload_from_state(baseline_state))
        content = render_state_payload_block(wrapper="session_state", state_payload=payload)
        if not content:
            return None
        return ContextBlock(
            id="session_state",
            zone="dynamic",
            source="session_state",
            content=content,
            stability="volatile",
            priority="high",
            cacheable=False,
            truncatable=True,
            metadata={
                "wrapper": "session_state",
                "delta_from_session_memory": baseline_state is not None,
            },
            state_payload=payload,
        )


class CompactionCheckpoint:
    def __init__(self, tree: SessionTree) -> None:
        self._snapshot = tree.model_copy(deep=True)

    def restore_into(self, tree: SessionTree) -> None:
        tree.entries.clear()
        tree.entries.update({k: v.model_copy(deep=True) for k, v in self._snapshot.entries.items()})
        tree.order = list(self._snapshot.order)
        tree.leaf_id = self._snapshot.leaf_id

    @property
    def snapshot(self) -> SessionTree:
        return self._snapshot


def take_checkpoint(tree: SessionTree) -> CompactionCheckpoint:
    return CompactionCheckpoint(tree)
