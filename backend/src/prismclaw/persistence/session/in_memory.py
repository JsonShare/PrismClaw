"""进程内 ``SessionStore``：给「不落库但需要完整凭证语义」的子代理 run 使用。

同步子代理（``sub_agent`` 工具）不需要跨 turn 存活，但需要与持久路径共用同一套
``SubagentRunContext``：工具结果有落点、被拒调用有配对、消息可重建。把存储换成
本实现即可，无需改动 ``SubagentPersistence`` 或循环骨架。

实现范围：``SessionStore`` 协议全部方法。会话树与事件只在内存，随 run 回收。
"""

from __future__ import annotations

import uuid

from prismclaw.schema.agent.session import SessionHistorySummary
from prismclaw.schema.session.types import (
    SessionEvent,
    SessionEventType,
    SessionHeader,
    SessionTree,
)


class InMemorySessionStore:
    """最小可用的内存会话存储。

    与 Redis 实现的行为对齐点：

    - ``commit_events`` 为每条事件分配单调递增的 ``seq``（每条事件 = 一条投影），
      返回**按入参顺序逐条对应**的副本——``SubagentPersistence.commit_entries``
      依赖这个「一进一出」约定（长度不符会直接抛错）。
    - ``create_new_session`` 写一条 ``session_created`` 事件并记录 session_key 指针。
    """

    def __init__(self) -> None:
        self._trees: dict[str, SessionTree] = {}
        self._events: dict[str, list[SessionEvent]] = {}
        self._current: dict[str, str] = {}

    # ── Read path ──────────────────────────────────────────────
    async def load(self, session_id: str) -> SessionTree | None:
        return self._trees.get(session_id)

    async def save_header(self, tree: SessionTree) -> None:
        self._trees[tree.header.id] = tree

    # ── Unified write gateway ──────────────────────────────────
    async def commit_events(self, events: list[SessionEvent]) -> list[SessionEvent]:
        committed: list[SessionEvent] = []
        for event in events:
            log = self._events.setdefault(event.session_id, [])
            stored = event.model_copy(update={"seq": len(log)})
            log.append(stored)
            committed.append(stored)
        return committed

    # ── Low-level event log ────────────────────────────────────
    async def append_event(self, event: SessionEvent) -> None:
        await self.commit_events([event])

    async def append_events_batch(self, events: list[SessionEvent]) -> None:
        await self.commit_events(list(events))

    # ── Event log queries ──────────────────────────────────────
    async def get_events(self, session_id: str, *, since_seq: int = 0) -> list[SessionEvent]:
        return [event for event in self._events.get(session_id, []) if event.seq >= since_seq]

    async def get_events_by_time(
        self, session_id: str, *, since_ms: int = 0, until_ms: int = 0
    ) -> list[SessionEvent]:
        result: list[SessionEvent] = []
        for event in self._events.get(session_id, []):
            stamp = getattr(event, "timestamp_ms", 0) or 0
            if since_ms and stamp < since_ms:
                continue
            if until_ms and stamp > until_ms:
                continue
            result.append(event)
        return result

    async def get_event_count(self, session_id: str) -> int:
        return len(self._events.get(session_id, []))

    async def rebuild_tree(self, session_id: str) -> SessionTree | None:
        # 树由调用方（SubagentPersistence）就地维护，这里只回放已持有的对象。
        return self._trees.get(session_id)

    # ── Session lifecycle ──────────────────────────────────────
    async def get_current_session_id(self, session_key: str) -> str | None:
        return self._current.get(session_key)

    async def set_current_session_id(self, session_key: str, session_id: str) -> None:
        self._current[session_key] = session_id

    async def create_new_session(
        self,
        session_key: str,
        workspace_id: str,
        agent_id: str,
        parent_session_id: str | None = None,
        title: str | None = None,
    ) -> SessionTree:
        session_id = f"{session_key}:{uuid.uuid4().hex[:12]}"
        header = SessionHeader(
            id=session_id,
            workspace_id=workspace_id,
            agent_id=agent_id,
            session_key=session_key,
            parent_session=parent_session_id,
            title=title,
        )
        tree = SessionTree(header=header)
        self._trees[session_id] = tree
        await self.commit_events(
            [
                SessionEvent(
                    seq=0,
                    type=SessionEventType.SESSION_CREATED,
                    session_id=session_id,
                    data={"type": "session_created", "header": header.model_dump()},
                )
            ]
        )
        await self.set_current_session_id(session_key, session_id)
        return tree

    async def list_session_history(
        self, session_key: str, limit: int = 20, offset: int = 0
    ) -> list[SessionHistorySummary]:
        # 内存存储不服务历史列表（子代理会话在 run 结束后即回收）。
        return []

    async def delete_session(self, session_id: str) -> bool:
        self._events.pop(session_id, None)
        return self._trees.pop(session_id, None) is not None


__all__ = ["InMemorySessionStore"]
