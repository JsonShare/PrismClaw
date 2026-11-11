from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol, runtime_checkable

from prismclaw.engine.execution.handoff.contract import HandoffRequestEntry

logger = logging.getLogger(__name__)


_REDIS_KEY_PREFIX = "prismclaw:handoff:pending:"
_REDIS_LOOKUP_PREFIX = "prismclaw:handoff:session_lookup:"


def _session_hash(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:32]


def _task_hash(task: str) -> str:
    return hashlib.sha256(task.encode("utf-8")).hexdigest()[:12]


def composite_subkey(
    *, source_sub_session_id: str, target: str, task: str
) -> str:
    return f"{source_sub_session_id}|{target}|{_task_hash(task)}"


@runtime_checkable
class _RedisLike(Protocol):
    async def setex(self, key: str, ttl: int, value: str) -> Any: ...
    async def get(self, key: str) -> Any: ...
    async def delete(self, *keys: str) -> Any: ...
    async def hgetall(self, key: str) -> Any: ...
    async def hset(self, key: str, mapping: dict[str, str]) -> Any: ...
    async def expire(self, key: str, ttl: int) -> Any: ...


@dataclass
class _SessionState:
    entries: dict[str, HandoffRequestEntry] = field(default_factory=dict)
    insertion_seq: int = 0


class PendingHandoffStore:
    def __init__(
        self,
        *,
        redis_client: _RedisLike | None = None,
        ttl_seconds: int = 1800,
        reverse_lookup_enabled: bool = True,
    ) -> None:
        self._memory: dict[str, _SessionState] = {}
        self._redis = redis_client
        self._ttl_seconds = ttl_seconds
        self._reverse_lookup_enabled = reverse_lookup_enabled

    @property
    def ttl_seconds(self) -> int:
        return self._ttl_seconds

    async def insert(
        self,
        *,
        session_id: str,
        entry: HandoffRequestEntry,
        current_turn: int,
    ) -> str:
        state = self._memory.setdefault(session_id, _SessionState())
        state.insertion_seq += 1
        entry.insertion_seq = state.insertion_seq
        entry.insertion_turn = current_turn
        subkey = composite_subkey(
            source_sub_session_id=entry.source_sub_session_id,
            target=entry.target,
            task=entry.task,
        )
        state.entries[subkey] = entry
        await self._persist_session_to_redis(session_id, state)
        return subkey

    async def get_all(self, session_id: str) -> list[HandoffRequestEntry]:
        state = self._memory.get(session_id)
        if state is None:
            return []
        return list(state.entries.values())

    async def consume(self, session_id: str, subkey: str) -> bool:
        state = self._memory.get(session_id)
        if state is None or subkey not in state.entries:
            return False
        del state.entries[subkey]
        await self._persist_session_to_redis(session_id, state)
        return True

    async def clear_session(self, session_id: str) -> int:
        state = self._memory.pop(session_id, None)
        n_cleared = len(state.entries) if state is not None else 0
        if self._redis is not None:
            session_hash = _session_hash(session_id)
            keys_to_delete = [_REDIS_KEY_PREFIX + session_hash]
            if self._reverse_lookup_enabled:
                keys_to_delete.append(_REDIS_LOOKUP_PREFIX + session_hash)
            try:
                await self._redis.delete(*keys_to_delete)
            except Exception:
                logger.warning(
                    "handoff_store.clear_session.redis_failed session_id=%s",
                    session_id,
                    exc_info=True,
                )
        return n_cleared

    async def expire_aged(
        self, session_id: str, current_turn: int, ttl_turns: int
    ) -> list[HandoffRequestEntry]:
        state = self._memory.get(session_id)
        if state is None:
            return []
        expired: list[HandoffRequestEntry] = []
        survivors: dict[str, HandoffRequestEntry] = {}
        for subkey, entry in state.entries.items():
            age = current_turn - entry.insertion_turn
            if age >= ttl_turns:
                expired.append(entry)
            else:
                survivors[subkey] = entry
        if expired:
            state.entries = survivors
            await self._persist_session_to_redis(session_id, state)
        return expired

    async def hydrate_from_redis(self, session_id: str) -> int:
        if self._redis is None:
            return 0
        session_hash = _session_hash(session_id)
        key = _REDIS_KEY_PREFIX + session_hash
        try:
            payload = await self._redis.get(key)
        except Exception:
            logger.warning(
                "handoff_store.hydrate.redis_failed session_id=%s",
                session_id,
                exc_info=True,
            )
            return 0
        if not payload:
            return 0
        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            logger.warning(
                "handoff_store.hydrate.json_parse_failed session_id=%s", session_id
            )
            return 0
        state = _SessionState()
        for entry_dict in data.get("entries", []):
            try:
                entry = HandoffRequestEntry(**entry_dict)
            except TypeError:
                continue
            subkey = composite_subkey(
                source_sub_session_id=entry.source_sub_session_id,
                target=entry.target,
                task=entry.task,
            )
            state.entries[subkey] = entry
            if entry.insertion_seq > state.insertion_seq:
                state.insertion_seq = entry.insertion_seq
        self._memory[session_id] = state
        return len(state.entries)

    async def _persist_session_to_redis(
        self, session_id: str, state: _SessionState
    ) -> None:
        if self._redis is None:
            return
        session_hash = _session_hash(session_id)
        primary_key = _REDIS_KEY_PREFIX + session_hash
        try:
            if not state.entries:
                keys_to_delete = [primary_key]
                if self._reverse_lookup_enabled:
                    keys_to_delete.append(_REDIS_LOOKUP_PREFIX + session_hash)
                await self._redis.delete(*keys_to_delete)
                return
            payload = json.dumps(
                {
                    "insertion_seq": state.insertion_seq,
                    "entries": [asdict(e) for e in state.entries.values()],
                },
                separators=(",", ":"),
            )
            await self._redis.setex(primary_key, self._ttl_seconds, payload)
            if self._reverse_lookup_enabled:
                lookup_key = _REDIS_LOOKUP_PREFIX + session_hash
                await self._redis.setex(lookup_key, self._ttl_seconds, session_id)
        except Exception:
            logger.warning(
                "handoff_store.persist.redis_failed session_id=%s",
                session_id,
                exc_info=True,
            )
__all__ = [
    "HandoffRequestEntry",
    "PendingHandoffStore",
    "composite_subkey",
]
