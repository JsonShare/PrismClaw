"""会话事件 seq 落到 entry 投影的单测。

`seq` 是客户端增量对账的唯一坐标：事件日志本来就有 seq，但投影出的 entry
没有记录，前端拿不到"这条消息对应哪次事件"。这里锁住两条投影路径
（Redis 写入投影 / 内存 from_events 回放）必须给出同一个 seq。
"""

from __future__ import annotations

import asyncio
import json

from prismclaw.persistence.session.redis import RedisSessionStore
from prismclaw.schema.agent.session import SessionEvent
from prismclaw.schema.session.types import MessageEntry, SessionEventType, SessionTree


class _FakePipeline:
    """按入队顺序立刻执行的极简 pipeline（无事务语义，仅为满足调用形态）。"""

    def __init__(self, client: _MiniRedisClient) -> None:
        self._client = client
        self._ops: list[tuple[str, tuple, dict]] = []

    async def __aenter__(self) -> _FakePipeline:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False

    def __getattr__(self, name: str):
        def _enqueue(*args, **kwargs):
            self._ops.append((name, args, kwargs))
            return self

        return _enqueue

    async def execute(self) -> list:
        results = []
        for name, args, kwargs in self._ops:
            results.append(await getattr(self._client, name)(*args, **kwargs))
        self._ops.clear()
        return results


class _MiniRedisClient:
    def __init__(self) -> None:
        self.kv: dict[str, object] = {}
        self.hashes: dict[str, dict[str, object]] = {}
        self.lists: dict[str, list[object]] = {}

    def pipeline(self, *, transaction: bool = False) -> _FakePipeline:
        _ = transaction
        return _FakePipeline(self)

    async def get(self, key: str):
        return self.kv.get(key)

    async def set(self, key: str, value, ex=None) -> bool:
        _ = ex
        self.kv[key] = value
        return True

    async def expire(self, key: str, ttl: int) -> bool:
        _ = key, ttl
        return True

    async def hset(self, key: str, field=None, value=None, *, mapping=None) -> int:
        bucket = self.hashes.setdefault(key, {})
        if mapping is not None:
            bucket.update(mapping)
        else:
            bucket[field] = value
        return 1

    async def rpush(self, key: str, *values) -> int:
        self.lists.setdefault(key, []).extend(values)
        return len(self.lists[key])

    async def hgetall(self, key: str) -> dict:
        return dict(self.hashes.get(key, {}))


def _event(seq: int, etype: SessionEventType, data: dict) -> SessionEvent:
    return SessionEvent(seq=seq, type=etype, session_id="s1", data=data)


def _events() -> list[SessionEvent]:
    return [
        _event(1, SessionEventType.USER_MESSAGE, {"entry_id": "u1", "content": "hi"}),
        _event(
            2,
            SessionEventType.ASSISTANT_MESSAGE,
            {"entry_id": "a1", "content": "first", "tool_calls": [{"id": "tc1"}]},
        ),
        _event(
            3,
            SessionEventType.TOOL_RESULT,
            {"entry_id": "t1", "content": "ok", "tool_call_id": "tc1"},
        ),
        # 更新事件覆盖 a1 的 seq：压缩/执行时间线等后到信息落在同一条 entry 上。
        _event(
            4,
            SessionEventType.ENTRY_UPDATED,
            {
                "entry": {
                    "id": "a1",
                    "type": "message",
                    "role": "assistant",
                    "content": "first",
                    "parent_id": "u1",
                },
                "leaf_id": "a1",
            },
        ),
        # 无法解析成类型化 entry 的更新事件走 json.dumps 兜底，同样要盖章。
        _event(5, SessionEventType.ENTRY_UPDATED, {"entry": {"id": "x1", "type": "unknown"}}),
    ]


def _run_projection(events: list[SessionEvent]) -> tuple[dict[str, dict], list[str]]:
    client = _MiniRedisClient()
    store = RedisSessionStore(client, lock_manager=None, ttl_seconds=60, key_prefix="test:")

    async def _go() -> None:
        await store._apply_projections_from_events("s1", events)

    asyncio.run(_go())
    raw = client.hashes[store._entries_key("s1")]
    parsed = {eid: json.loads(value) for eid, value in raw.items()}
    return parsed, client.lists[store._order_key("s1")]


def test_redis_projection_stamps_insert_event_seq() -> None:
    parsed, order = _run_projection(_events())
    assert parsed["u1"]["seq"] == 1
    assert parsed["t1"]["seq"] == 3
    # a1 被 seq=4 的 ENTRY_UPDATED 覆盖
    assert parsed["a1"]["seq"] == 4
    assert order == ["u1", "a1", "t1"]


def test_redis_projection_stamps_unparsed_entry_without_mutating_event() -> None:
    events = _events()
    parsed, _ = _run_projection(events)
    assert parsed["x1"]["seq"] == 5
    # 兜底分支必须先 copy，不能把 seq 写回调用方传入的 dict
    assert "seq" not in events[-1].data["entry"]


def test_from_events_matches_redis_projection() -> None:
    events = _events()
    tree = SessionTree.from_events(events)
    parsed, _ = _run_projection(events)
    for entry_id in ("u1", "a1", "t1"):
        entry = tree.entries[entry_id]
        assert isinstance(entry, MessageEntry)
        assert entry.seq == parsed[entry_id]["seq"]


def test_from_events_entry_updated_overrides_seq() -> None:
    events = [
        _event(1, SessionEventType.ASSISTANT_MESSAGE, {"entry_id": "a1", "content": "x"}),
        _event(
            7,
            SessionEventType.ENTRY_UPDATED,
            {
                "entry": {
                    "id": "a1",
                    "parent_id": "u0",
                    "type": "message",
                    "role": "assistant",
                    "content": "x",
                }
            },
        ),
    ]
    tree = SessionTree.from_events(events)
    assert tree.entries["a1"].seq == 7


def test_legacy_entries_default_to_zero() -> None:
    # 默认值语义：0 = 本次改造前的历史数据（未知），不参与增量过滤
    assert MessageEntry(id="m", parent_id=None, role="user", content="").seq == 0
