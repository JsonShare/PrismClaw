from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import redis.asyncio as aioredis

from prismclaw.schema.session.types import SessionTree
from prismclaw.schema.agent.session import SessionHeader
from prismclaw.schema.session.types import SessionEntry
from prismclaw.schema.session import (
    SessionEvent,
    SessionEventType,
    SessionHistorySummary,
    _event_to_message_entry,
    _parse_entry as _parse_entry_dict,
    _repair_parent_chain,
)
from prismclaw.persistence.lock.redis import LockAcquireError
from prismclaw.schema.persistence.persistence import LockManager
from prismclaw.persistence.session.base import emit_commit_metrics, _generate_session_id

logger = logging.getLogger(__name__)


def _redis_elapsed_ms(started: float) -> float:
    """Return ``(now - started)`` in milliseconds."""
    return (time.monotonic() - started) * 1000.0


class SessionLockError(Exception):
    def __init__(self, session_id: str) -> None:
        super().__init__(f"could not acquire write lock for session {session_id!r}")
        self.session_id = session_id


class RedisSessionStore:
    def __init__(
        self,
        client: aioredis.Redis,
        lock_manager: LockManager,
        *,
        ttl_seconds: int = 604_800,
        key_prefix: str = "prismclaw:",
    ) -> None:
        self._client = client
        self._lock = lock_manager
        self._ttl = ttl_seconds
        self._prefix = key_prefix

    def _hdr_key(self, sid: str) -> str:
        return f"{self._prefix}session:{{{sid}}}:header"

    def _entries_key(self, sid: str) -> str:
        return f"{self._prefix}session:{{{sid}}}:entries"

    def _order_key(self, sid: str) -> str:
        return f"{self._prefix}session:{{{sid}}}:order"

    def _leaf_key(self, sid: str) -> str:
        return f"{self._prefix}session:{{{sid}}}:leaf"

    def _todos_key(self, sid: str) -> str:
        return f"{self._prefix}session:{{{sid}}}:todos"

    def _events_key(self, sid: str) -> str:
        return f"{self._prefix}session:{{{sid}}}:events"

    def _event_seq_key(self, sid: str) -> str:
        return f"{self._prefix}session:{{{sid}}}:event_seq"

    def _lock_key(self, sid: str) -> str:
        return f"session-lock:{{{sid}}}"

    def _skey_current_key(self, session_key: str) -> str:
        return f"{self._prefix}skey:{{{session_key}}}:current"

    def _skey_history_key(self, session_key: str) -> str:
        return f"{self._prefix}skey:{{{session_key}}}:history"

    # ── Projection: Fast read path ─────────────────────────────────────

    async def save_header(self, tree: SessionTree) -> None:
        sid = tree.header.id
        hdr_json = tree.header.model_dump_json()
        # events 永不过期（TTL=-1），与 event_seq 对齐；source of truth 必须永久保留
        async with self._client.pipeline(transaction=False) as pipe:
            pipe.set(self._hdr_key(sid), hdr_json, ex=self._ttl)
            pipe.expire(self._entries_key(sid), self._ttl)
            pipe.expire(self._order_key(sid), self._ttl)
            pipe.expire(self._leaf_key(sid), self._ttl)
            await pipe.execute()

    async def load(self, session_id: str) -> SessionTree | None:
        async with self._client.pipeline(transaction=False) as pipe:
            pipe.get(self._hdr_key(session_id))
            pipe.hgetall(self._entries_key(session_id))
            pipe.lrange(self._order_key(session_id), 0, -1)
            pipe.get(self._leaf_key(session_id))
            pipe.get(self._todos_key(session_id))
            hdr_raw, entries_raw, order_raw, leaf_raw, todos_raw = await pipe.execute()

        if hdr_raw is None:
            return None

        header = SessionHeader.model_validate_json(hdr_raw)
        entries: dict[str, SessionEntry] = {}
        for entry_id, entry_json in (entries_raw or {}).items():
            entry = _parse_entry(entry_json)
            if entry is not None:
                entries[entry_id] = entry

        todos: list[dict[str, Any]] = []
        if todos_raw:
            try:
                todos = json.loads(todos_raw)
            except Exception:
                pass

        tree = SessionTree(
            header=header,
            entries=entries,
            order=list(order_raw or []),
            leaf_id=leaf_raw or None,
            todos=todos,
        )
        # Legacy sessions persisted before message events carried parent_id
        # load back with a broken branch chain (cross-turn amnesia); relink
        # from order. No-op for entries that already carry parent_id.
        _repair_parent_chain(tree)
        return tree

    # ── Event Sourcing: Source of Truth (low-level) ─────────────────────
    # These are low-level Event Log write methods WITHOUT lock or projection.
    # For the full write path (lock + log + projection), use commit_events().
    # These are reserved for lifecycle operations (create/delete session).

    async def append_event(self, event: SessionEvent) -> None:
        """Append a single event to the Event Log. No lock or projection."""
        await self._persist_events_to_log(event.session_id, [event])

    async def append_events_batch(self, events: list[SessionEvent]) -> None:
        """Append multiple events to the Event Log. No lock or projection."""
        if not events:
            return
        await self._persist_events_to_log(events[0].session_id, events)

    async def get_events(self, session_id: str, *, since_seq: int = 0) -> list[SessionEvent]:
        """Read events from the Event Log, optionally since a given seq."""
        raw = await self._client.lrange(self._events_key(session_id), 0, -1)
        if not raw:
            return []
        events: list[SessionEvent] = []
        for item in raw:
            try:
                data = json.loads(item)
                event = SessionEvent.model_validate(data)
                if event.seq >= since_seq:
                    events.append(event)
            except Exception:
                logger.warning("Failed to parse event for session %s", session_id)
        return events

    async def get_events_by_time(
        self,
        session_id: str,
        *,
        since_ms: int = 0,
        until_ms: int = 0,
    ) -> list[SessionEvent]:
        """Read events from the Event Log filtered by time range (epoch ms).

        Aligns with DSH time-seeking: events are filtered by time_ms
        and returned in seq order (monotonically increasing with time).
        """
        raw = await self._client.lrange(self._events_key(session_id), 0, -1)
        if not raw:
            return []
        events: list[SessionEvent] = []
        for item in raw:
            try:
                data = json.loads(item)
                event = SessionEvent.model_validate(data)
                if since_ms > 0 and event.time < since_ms:
                    continue
                if until_ms > 0 and event.time > until_ms:
                    continue
                events.append(event)
            except Exception:
                logger.warning("Failed to parse event for session %s", session_id)
        return events

    async def get_event_count(self, session_id: str) -> int:
        """Return the total number of events for a session."""
        raw = await self._client.lrange(self._events_key(session_id), 0, -1)
        return len(raw) if raw else 0

    async def rebuild_tree(self, session_id: str) -> SessionTree | None:
        """Rebuild SessionTree from the Event Log (read-time projection).

        Processes events in seq order to reconstruct the current state.
        This is the recovery path when the direct storage projection is
        lost or out-of-sync. Uses SessionTree.from_events() which is the
        canonical projection from SessionEvent stream.
        """
        events = await self.get_events(session_id)
        if not events:
            return None

        tree = SessionTree.from_events(events)

        if tree.header.id == "":
            hdr_raw = await self._client.get(self._hdr_key(session_id))
            if hdr_raw:
                tree.header = SessionHeader.model_validate_json(hdr_raw)

        if tree.header.id == "":
            return None

        return tree

    # ── Unified Write Gateway ──────────────────────────────────────────
    # commit_events is the SINGLE write entry point. It handles:
    #   1. Distributed lock acquisition
    #   2. Event Log persistence (source of truth, immutable)
    #   3. Projection updates (derived read state)
    #   4. Lock release
    #
    # Projection is automatically derived from event payloads — callers
    # do NOT pass leaf_id, is_update, or other projection context.

    # 锁竞争重试参数：同一 session 的并发写方（主循环工具写树 vs 后台
    # run_projection flusher 落盘）可能瞬时撞锁。锁是 nx 一次性尝试、持有方
    # 毫秒级释放，因此短退避重试即可让双方都成功，而不是让其中一个硬失败。
    # 总预算 ~3s << 锁 ttl 30s，不会死等。
    _LOCK_RETRY_ATTEMPTS = 8
    _LOCK_RETRY_BASE_DELAY_S = 0.02

    async def _acquire_with_retry(self, lock_key: str, session_id: str) -> str:
        """带短退避重试地获取写锁；耗尽重试次数仍失败才抛 SessionLockError。

        原实现 `acquire` 失败即抛，导致并发写方（主循环 / flusher）中后到者
        瞬时失败、run 崩溃。这里退化为「短暂等待后重试」，语义对齐
        commit_events 的「可靠写入门」定位。
        """
        for attempt in range(self._LOCK_RETRY_ATTEMPTS):
            try:
                return await self._lock.acquire(lock_key, ttl_ms=30_000)
            except LockAcquireError:
                if attempt == self._LOCK_RETRY_ATTEMPTS - 1:
                    break
                # 递增退避：0.02 * 2^attempt，封顶约 1s
                delay = min(self._LOCK_RETRY_BASE_DELAY_S * (2 ** attempt), 1.0)
                await asyncio.sleep(delay)
        raise SessionLockError(session_id)

    async def commit_events(self, events: list[SessionEvent]) -> list[SessionEvent]:
        """Commit events: Event Log → Projection, under distributed lock.

        The projection is automatically derived from event types and payloads:
        - ENTRY_ADDED: adds entry to entries hash, appends to order, updates leaf
        - ENTRY_UPDATED: patches entry in entries hash, optionally updates leaf
        - HEADER_UPDATED: updates header key
        - SESSION_CREATED: creates new session header

        Returns the events with their assigned sequence numbers.
        """
        if not events:
            return events

        session_id = events[0].session_id
        if not all(e.session_id == session_id for e in events):
            raise ValueError("All events must belong to the same session")

        started = time.monotonic()
        lock_key = self._lock_key(session_id)
        token = await self._acquire_with_retry(lock_key, session_id)

        try:
            # 提交时 parent 重锚：写入方各自用（可能过期的）内存 tree.leaf_id
            # 构造 parent_id，交错提交会产生兄弟 fork，fork 上的 entry 对
            # get_branch() 类读取不可见。此处在锁内把 parent 重锚到持久化
            # leaf 真值，对所有写入方统一生效（详见 _rebase_entry_parents）。
            await self._rebase_entry_parents(session_id, events)
            await self._persist_events_to_log(session_id, events)
            await self._apply_projections_from_events(session_id, events)
        except Exception:
            raise
        finally:
            await self._lock.release(lock_key, token)

        emit_commit_metrics(len(events), _redis_elapsed_ms(started))
        return events

    async def _rebase_entry_parents(self, session_id: str, events: list[SessionEvent]) -> None:
        """提交时 parent 重锚：关闭「写侧 fork 竞态」这一结构性缺陷。

        问题描述：所有写入方都用各自（可能过期的）内存 tree.leaf_id 构造
        entry 的 parent_id 再提交。同一 run 内的连续写入（task bootstrap 的
        task_spec / review_requirements / task_runtime_snapshot / evidence /
        decision_ledger / run_snapshot）互不推进内存 leaf，全部挂在同一个
        旧 tip 上互为兄弟 fork；与 run_projection flusher 交错提交时 fork
        更严重。fork 上的 entry 不在 root→leaf 分支上，get_branch() 类读取
        （GET /run 构建 task_runtime 的树读取、
        get_latest_task_runtime 等）全部失明——刷新页面恢复执行中状态因此
        永远失败。

        修复边界：commit_events 是唯一写入门且已持 session 锁。在此把每条
        entry 的 parent_id 重锚到「持久化 leaf 的当前真值」：所有写入方
        无需改动即被治愈，事件日志与投影使用同一份重锚后的 entry，保持
        一致。批量内显式链式（parent 指向同批已提交的 entry，如
        project_tool_result_entries 把 tool_result 依次挂到前一条上）通过
        batch_ids 识别并保留。
        """
        raw = await self._client.get(self._leaf_key(session_id))
        tip: str | None = raw.decode() if isinstance(raw, bytes) else raw
        batch_ids: set[str] = set()
        for event in events:
            data = event.data
            etype = event.type
            if etype == SessionEventType.ENTRY_UPDATED:
                entry_data = data.get("entry")
                if not isinstance(entry_data, dict):
                    continue
                entry_id = entry_data.get("id")
                if not entry_id:
                    continue
                if entry_data.get("parent_id") not in batch_ids:
                    entry_data["parent_id"] = tip
                batch_ids.add(entry_id)
                advanced = data.get("leaf_id")
                if isinstance(advanced, str) and advanced:
                    tip = advanced
            elif etype in (
                SessionEventType.USER_MESSAGE,
                SessionEventType.ASSISTANT_MESSAGE,
                SessionEventType.TOOL_RESULT,
            ):
                entry_id = data.get("entry_id")
                if not entry_id:
                    continue
                if data.get("parent_id") not in batch_ids:
                    data["parent_id"] = tip
                batch_ids.add(entry_id)
                tip = entry_id

    async def _persist_events_to_log(self, session_id: str, events: list[SessionEvent]) -> None:
        """Write events to Event Log with atomic seq generation.

        seq numbers are assigned via Redis INCR, guaranteeing monotonic
        increase across distributed workers (B/S architecture).
        """
        seq_key = self._event_seq_key(session_id)

        for event in events:
            next_seq = await self._client.incr(seq_key)
            event.seq = int(next_seq)

        async with self._client.pipeline(transaction=True) as pipe:
            for event in events:
                pipe.rpush(self._events_key(session_id), event.model_dump_json())
            # events 永不过期（TTL=-1），与 event_seq 对齐
            await pipe.execute()

    async def _apply_projections_from_events(self, session_id: str, events: list[SessionEvent]) -> None:
        """Apply projection updates derived from event data (DSH-aligned).

        Events that produce surface entries:
        - user/message → MessageEntry(role=user)
        - assistant/message → MessageEntry(role=assistant)
        - tool/result → MessageEntry(role=tool)
        - entry_updated → patch existing entry

        Events that update header:
        - request/header, session_created → update SessionHeader

        Log-only events (no projection; may exist in persisted event logs):
        - turn/start, turn/end, step/start, step/end (factory removed, legacy data only)
        - assistant/chunk
        - request/context, todo/write, session/end-seed, session_deleted
        """
        entries_to_add: list[tuple[str, str]] = []
        entries_to_update: list[tuple[str, str]] = []
        order_ids: list[str] = []
        leaf_id: str | None = None
        msg_count_incr = 0
        header_event: SessionEvent | None = None
        todos_data: list[dict[str, Any]] | None = None

        for event in events:
            data = event.data
            etype = event.type

            if etype == SessionEventType.USER_MESSAGE:
                entry = _event_to_message_entry(data, role="user")
                if entry:
                    entries_to_add.append((entry.id, entry.model_dump_json()))
                    order_ids.append(entry.id)
                    leaf_id = entry.id
                    msg_count_incr += 1

            elif etype == SessionEventType.ASSISTANT_MESSAGE:
                entry = _event_to_message_entry(data, role="assistant")
                if entry:
                    entries_to_add.append((entry.id, entry.model_dump_json()))
                    order_ids.append(entry.id)
                    leaf_id = entry.id
                    msg_count_incr += 1

            elif etype == SessionEventType.TOOL_RESULT:
                entry = _event_to_message_entry(data, role="tool")
                if entry:
                    entries_to_add.append((entry.id, entry.model_dump_json()))
                    order_ids.append(entry.id)
                    leaf_id = entry.id
                    msg_count_incr += 1

            elif etype == SessionEventType.ENTRY_UPDATED:
                entry_data = data.get("entry", {})
                entry_id = entry_data.get("id", "")
                if entry_id:
                    typed_entry = _parse_entry_dict(entry_data)
                    if typed_entry:
                        entries_to_update.append((entry_id, typed_entry.model_dump_json()))
                    else:
                        entries_to_update.append((entry_id, json.dumps(entry_data)))
                if "leaf_id" in data:
                    leaf_id = data["leaf_id"]

            elif etype in (SessionEventType.REQUEST_HEADER, SessionEventType.SESSION_CREATED):
                header_event = event

            elif etype == SessionEventType.TODO_WRITE:
                todos_data = data.get("todos", [])

        hdr_raw = await self._client.get(self._hdr_key(session_id))

        async with self._client.pipeline(transaction=False) as pipe:
            if entries_to_add:
                pipe.hset(self._entries_key(session_id), mapping=dict(entries_to_add))
                pipe.rpush(self._order_key(session_id), *order_ids)
            if entries_to_update:
                pipe.hset(self._entries_key(session_id), mapping=dict(entries_to_update))
            if leaf_id is not None:
                pipe.set(self._leaf_key(session_id), leaf_id)
            if msg_count_incr > 0 and hdr_raw:
                header = SessionHeader.model_validate_json(hdr_raw)
                header.message_count = (header.message_count or 0) + msg_count_incr
                pipe.set(self._hdr_key(session_id), header.model_dump_json(), ex=self._ttl)
            if header_event is not None:
                header_data = header_event.data.get("header")
                if header_data:
                    if header_event.type == SessionEventType.SESSION_CREATED:
                        header = SessionHeader.model_validate(header_data)
                    elif hdr_raw:
                        existing = SessionHeader.model_validate_json(hdr_raw)
                        header = existing.model_copy(update=header_data)
                    else:
                        header = SessionHeader.model_validate(header_data)
                    pipe.set(self._hdr_key(session_id), header.model_dump_json(), ex=self._ttl)
            if todos_data is not None:
                pipe.set(self._todos_key(session_id), json.dumps(todos_data), ex=self._ttl)

            pipe.expire(self._entries_key(session_id), self._ttl)
            if leaf_id is not None:
                pipe.expire(self._leaf_key(session_id), self._ttl)
            if entries_to_add:
                pipe.expire(self._order_key(session_id), self._ttl)

            await pipe.execute()

    # ── Session lifecycle ───────────────────────────────────────────────

    async def get_current_session_id(self, session_key: str) -> str | None:
        val = await self._client.get(self._skey_current_key(session_key))
        if val is None:
            return None
        return val.decode() if isinstance(val, bytes) else val

    async def set_current_session_id(self, session_key: str, session_id: str) -> None:
        score = time.time() * 1000
        async with self._client.pipeline(transaction=False) as pipe:
            pipe.set(self._skey_current_key(session_key), session_id)
            pipe.zadd(self._skey_history_key(session_key), {session_id: score})
            await pipe.execute()

    async def create_new_session(
        self,
        session_key: str,
        workspace_id: str,
        agent_id: str,
        parent_session_id: str | None = None,
        title: str | None = None,
    ) -> SessionTree:
        session_id = _generate_session_id(session_key)
        header = SessionHeader(
            id=session_id,
            workspace_id=workspace_id,
            agent_id=agent_id,
            session_key=session_key,
            parent_session=parent_session_id,
            title=title,
        )
        tree = SessionTree(header=header)

        event = SessionEvent(
            seq=0,
            type=SessionEventType.SESSION_CREATED,
            session_id=session_id,
            data={"type": "session_created", "header": header.model_dump()},
        )
        await self.commit_events([event])

        await self.set_current_session_id(session_key, session_id)
        return tree

    async def list_session_history(
        self, session_key: str, limit: int = 20, offset: int = 0
    ) -> list[SessionHistorySummary]:
        raw = await self._client.zrevrangebyscore(
            self._skey_history_key(session_key),
            "+inf",
            "-inf",
            start=offset,
            num=limit if limit > 0 else 20,
        )
        if not raw:
            return []

        session_ids = [
            sid.decode() if isinstance(sid, bytes) else sid
            for sid in raw
        ]

        async with self._client.pipeline(transaction=False) as pipe:
            for sid in session_ids:
                pipe.get(self._hdr_key(sid))
            hdr_raw_list = await pipe.execute()

        result: list[SessionHistorySummary] = []
        needs_migration: list[tuple[int, str]] = []
        dead_ids: list[str] = []  # 会话投影已过期/缺失：历史 ZSET 悬空引用

        for i, sid in enumerate(session_ids):
            hdr_raw = hdr_raw_list[i]
            if hdr_raw is None:
                # 会话数据（header）已不存在（TTL 过期或被清理）。
                # 不再作为幽灵条目返回，交给下方惰性 ZREM 清掉历史悬空引用，
                # 避免会话列表展示空壳、前端反复 404。
                dead_ids.append(sid)
                continue

            header = SessionHeader.model_validate_json(hdr_raw)
            mc = header.message_count or 0

            if mc == 0 and header.last_interaction_at is not None:
                needs_migration.append((i, sid))

            result.append(
                SessionHistorySummary(
                    session_id=sid,
                    created_at=header.created_at,
                    message_count=mc,
                    last_message_at=header.last_interaction_at,
                    parent_session_id=header.parent_session,
                    title=header.title,
                )
            )

        if needs_migration:
            import asyncio

            _mig_logger = logging.getLogger(__name__)
            t = asyncio.create_task(
                self._migrate_message_counts(session_ids, hdr_raw_list, needs_migration)
            )

            def _on_mig_done(_t: asyncio.Task) -> None:
                try:
                    _t.result()
                except asyncio.CancelledError:
                    pass
                except Exception:
                    _mig_logger.debug("session message_count migration failed", exc_info=True)

            try:
                t.add_done_callback(_on_mig_done)
            except Exception:
                pass

        if dead_ids:
            # 惰性 GC：清掉历史 ZSET 中指向已丢失会话的悬空引用，
            # 使会话列表不再展示幽灵条目、前端不再对其反复 404。
            try:
                await self._client.zrem(self._skey_history_key(session_key), *dead_ids)
            except Exception:
                logger.debug("failed to GC dead session history refs", exc_info=True)

        return result

    async def _migrate_message_counts(
        self,
        session_ids: list[str],
        hdr_raw_list: list[bytes | None],
        needs_migration: list[tuple[int, str]],
    ) -> None:
        """按需计算 message_count 并回写 header（后台任务，不阻塞列表查询）。"""
        try:
            async with self._client.pipeline(transaction=False) as pipe:
                for _, sid in needs_migration:
                    pipe.hvals(self._entries_key(sid))
                entries_list = await pipe.execute()

            updates: list[tuple[int, int]] = []
            for (i, sid), ent_raw in zip(needs_migration, entries_list):
                msg_count = 0
                for raw in ent_raw:
                    try:
                        entry_data = json.loads(raw)
                        if entry_data.get("type") == "message":
                            msg_count += 1
                    except Exception:
                        pass
                if msg_count > 0:
                    updates.append((i, msg_count))

            if updates:
                async with self._client.pipeline(transaction=False) as pipe:
                    for i, new_count in updates:
                        hdr_raw = hdr_raw_list[i]
                        if hdr_raw is None:
                            continue
                        header = SessionHeader.model_validate_json(hdr_raw)
                        header.message_count = new_count
                        pipe.set(self._hdr_key(session_ids[i]), header.model_dump_json(), ex=self._ttl)
                    await pipe.execute()
        except Exception:
            logger.exception("Failed to migrate message_counts for %s", session_ids[0] if session_ids else "")

    async def delete_session(self, session_id: str) -> bool:
        hdr_raw = await self._client.get(self._hdr_key(session_id))
        if hdr_raw is None:
            return False
        try:
            header = SessionHeader.model_validate_json(hdr_raw)
            session_key = header.session_key
        except Exception:
            session_key = ""

        event = SessionEvent(
            seq=0,
            type=SessionEventType.SESSION_DELETED,
            session_id=session_id,
            data={"type": "session_deleted"},
        )
        await self.append_event(event)

        async with self._client.pipeline(transaction=False) as pipe:
            pipe.delete(self._hdr_key(session_id))
            pipe.delete(self._entries_key(session_id))
            pipe.delete(self._order_key(session_id))
            pipe.delete(self._leaf_key(session_id))
            if session_key:
                pipe.zrem(self._skey_history_key(session_key), session_id)
            await pipe.execute()
        if session_key:
            current = await self.get_current_session_id(session_key)
            if current == session_id:
                await self._client.delete(self._skey_current_key(session_key))
        return True


def _parse_entry(raw: str) -> SessionEntry | None:
    try:
        data = json.loads(raw)
        return _parse_entry_dict(data)
    except Exception:
        logger.exception("failed to parse session entry")
        return None
