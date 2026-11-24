"""会话增量同步（GET /conversations/{id}/sync）与 /messages 口径一致性单测。

设计见 docs/architecture/conversation-delivery.md §4.3 / §5.1：
- 权威层只认 seq，客户端靠 ``(conversation_id, after_seq)`` 收敛；
- user 与 assistant 同处 seq 体系，同步会返回两类消息；user 消息携带
  ``client_message_id``，客户端据此把本地乐观气泡 re-key 到服务端 entry；
- 运行中/无法确认时把本轮 assistant 标 in_progress，settled_seq 不得越过它。
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace

from prismclaw.app.web.chat.chat_protocol import SessionQueue
from prismclaw.app.web.routes import get_messages, sync_conversation
from prismclaw.schema.agent.session import SessionHeader
from prismclaw.schema.session.types import (
    EXECUTION_TIMELINE_CUSTOM_TYPE,
    CustomEntry,
    MessageEntry,
    SessionTree,
)


def _run(coro):
    return asyncio.run(coro)


CID = "web:admin:default:s:sync1"


def _tree(cid: str, entries: list) -> SessionTree:
    return SessionTree(
        header=SessionHeader(
            id=cid,
            workspace_id="default",
            agent_id="a1",
            session_key="web:admin:default",
        ),
        entries={entry.id: entry for entry in entries},
        # 与生产一致：CustomEntry 不进 order（它走 entry_updated 落库）。
        order=[entry.id for entry in entries if isinstance(entry, MessageEntry)],
        leaf_id=entries[-1].id if entries else None,
    )


def _user(
    entry_id: str,
    seq: int,
    content: str = "hi",
    client_message_id: str | None = None,
) -> MessageEntry:
    return MessageEntry(
        id=entry_id,
        parent_id=None,
        role="user",
        content=content,
        seq=seq,
        client_message_id=client_message_id,
    )


def _assistant(entry_id: str, seq: int, content: str) -> MessageEntry:
    return MessageEntry(id=entry_id, parent_id=None, role="assistant", content=content, seq=seq)


def _timeline(entry_id: str, message_id: str, seq: int) -> CustomEntry:
    return CustomEntry(
        id=entry_id,
        parent_id=None,
        custom_type=EXECUTION_TIMELINE_CUSTOM_TYPE,
        data={"message_id": message_id, "nodes": [{"id": "n1", "status": "done"}]},
        seq=seq,
    )


def _fake_request(
    session_queue: SessionQueue | None,
    presence: _FakePresence | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                web_deps=SimpleNamespace(
                    session_queue=session_queue,
                    review_utils=None,
                    review_registry_fn=None,
                    run_presence=presence,
                    # 装配展示消息会解析工作区根目录（构造 review registry 用）；
                    # 指向临时目录，避免测试触碰真实用户目录。
                    workspace_base=Path(tempfile.mkdtemp(prefix="pc-sync-test-")),
                )
            )
        )
    )


class _FakePresence:
    """模拟跨 worker 的 Redis 在场证明（只实现被端点消费的读接口）。"""

    def __init__(self, states: dict[str, str]) -> None:
        self._states = states

    async def read(self, conversation_id: str) -> str:
        return self._states.get(conversation_id, "idle")


def _patch_verify(monkeypatch, tree: SessionTree):
    import prismclaw.app.web.routes as routes_mod

    async def _fake_verify(*_args, **_kwargs):
        return tree

    monkeypatch.setattr(routes_mod, "_load_and_verify", _fake_verify)


def _activate(sq: SessionQueue, cid: str) -> None:
    # 与生产顺序一致：先建投影（细节 idle，流式生成形态），再置 RunControl。
    sq.get_or_create_web_runtime_projection(cid)
    sq.get_run_control(cid).active = True


def _sync(sq, *, cid: str = CID, after_seq: int = 0):
    return sync_conversation(
        _fake_request(sq),
        session_id=cid,
        workspace_id="default",
        after_seq=after_seq,
        user_id="admin",
    )


# ---------------------------------------------------------------------------
# 1. idle：无运行态，assistant 全部定稿
# ---------------------------------------------------------------------------


def test_idle_returns_no_run_and_all_settled(monkeypatch):
    tree = _tree(CID, [_user("u1", 1), _assistant("a1", 2, "答")])
    _patch_verify(monkeypatch, tree)

    async def run():
        res = await _sync(SessionQueue())
        assert res["run_state"] == "idle"
        assert res["run"] is None
        assert [m["id"] for m in res["messages"]] == ["u1", "a1"]
        assert all(m["in_progress"] is False for m in res["messages"])
        assert res["settled_seq"] == res["watermark"] == 2
        assert res["has_more"] is False
        assert res["history_reset"] is False

    _run(run())


# ---------------------------------------------------------------------------
# 2. active：本轮 assistant 标记 in_progress，settled_seq 不得越过它
# ---------------------------------------------------------------------------


def test_active_marks_tail_assistant_in_progress(monkeypatch):
    tree = _tree(CID, [_user("u1", 1), _assistant("a1", 5, "执行中…")])
    _patch_verify(monkeypatch, tree)

    async def run():
        sq = SessionQueue()
        _activate(sq, CID)
        res = await _sync(sq)
        assert res["run_state"] == "active"
        assert res["run"] is not None
        assert res["messages"][-1]["in_progress"] is True
        # 水位停在 in_progress 之前：客户端下次必须再取回这一轮；
        # 上一轮 user 消息（seq=1）已定稿，水位可推进到它。
        assert res["settled_seq"] == 1

    _run(run())


# ---------------------------------------------------------------------------
# 3. after_seq 增量：只返回 seq 更大的消息（user/assistant 同口径）
# ---------------------------------------------------------------------------


def test_after_seq_returns_only_newer_messages(monkeypatch):
    tree = _tree(
        CID,
        [
            _user("u1", 1),
            _assistant("a1", 2, "一"),
            _user("u2", 3),
            _assistant("a2", 4, "二"),
        ],
    )
    _patch_verify(monkeypatch, tree)

    async def run():
        res = await _sync(SessionQueue(), after_seq=2)
        assert [m["id"] for m in res["messages"]] == ["u2", "a2"]
        assert res["settled_seq"] == 4

    _run(run())


# ---------------------------------------------------------------------------
# 4. after_seq 超过水位：客户端本地 seq 失效
# ---------------------------------------------------------------------------


def test_after_seq_beyond_watermark_resets_history(monkeypatch):
    tree = _tree(CID, [_user("u1", 1), _assistant("a1", 2, "答")])
    _patch_verify(monkeypatch, tree)

    async def run():
        res = await _sync(SessionQueue(), after_seq=999)
        assert res["history_reset"] is True
        assert res["messages"] == []
        assert res["settled_seq"] == 999

    _run(run())


# ---------------------------------------------------------------------------
# 5. session_queue 缺失：unknown 且保守标记本轮 assistant
# ---------------------------------------------------------------------------


def test_unknown_state_marks_tail_assistant_conservatively(monkeypatch):
    tree = _tree(CID, [_user("u1", 1), _assistant("a1", 2, "可能还在跑")])
    _patch_verify(monkeypatch, tree)

    async def run():
        res = await _sync(None)
        assert res["run_state"] == "unknown"
        assert res["run"] is None
        assert res["messages"][-1]["in_progress"] is True
        assert res["settled_seq"] == 1

    _run(run())


# ---------------------------------------------------------------------------
# 6. 单页上限 100 条
# ---------------------------------------------------------------------------


def test_page_limit_sets_has_more(monkeypatch):
    entries = []
    for i in range(1, 121):
        entries.append(_user(f"u{i}", 2 * i - 1))
        entries.append(_assistant(f"a{i}", 2 * i, f"答{i}"))
    tree = _tree(CID, entries)
    _patch_verify(monkeypatch, tree)

    async def run():
        res = await _sync(SessionQueue())
        assert res["has_more"] is True
        assert len(res["messages"]) == 100
        assert res["messages"][0]["seq"] == 1
        assert res["messages"][-1]["seq"] == 100
        # settled_seq 只按本次返回的消息计算，避免客户端跳过未拿到的正文
        assert res["settled_seq"] == 100
        assert res["watermark"] == 240

    _run(run())


# ---------------------------------------------------------------------------
# 6b. 改造前的历史 entry（seq=0）不进同步：只经 /messages 提供
# ---------------------------------------------------------------------------


def test_legacy_seq_zero_entries_are_not_synced(monkeypatch):
    """否则老会话首页全是 seq=0，settled_seq 停在 0，新回答永远取不到。"""
    tree = _tree(
        CID,
        [
            _user("u1", 0, "旧问"),
            _assistant("a1", 0, "旧答"),
            _user("u2", 1, "新问"),
            _assistant("a2", 2, "新答"),
        ],
    )
    _patch_verify(monkeypatch, tree)

    async def run():
        res = await _sync(SessionQueue())
        assert [m["id"] for m in res["messages"]] == ["u2", "a2"]
        assert res["settled_seq"] == 2

    _run(run())


# ---------------------------------------------------------------------------
# 7. timeline 晚到：同一 message 的 seq 被抬高
# ---------------------------------------------------------------------------


def test_late_timeline_raises_message_seq(monkeypatch):
    tree = _tree(
        CID,
        [_user("u1", 1), _assistant("a1", 2, "答"), _timeline("c1", "a1", 10)],
    )
    _patch_verify(monkeypatch, tree)

    async def run():
        res = await _sync(SessionQueue(), after_seq=2)
        assert len(res["messages"]) == 1
        msg = res["messages"][0]
        assert msg["id"] == "a1"
        assert msg["seq"] == 10
        assert msg["timeline"]
        assert res["settled_seq"] == 10
        assert res["watermark"] == 10

    _run(run())


# ---------------------------------------------------------------------------
# 8. 后台子代理/工具痕迹仍在，但本轮已结束：必须按 turn 级判活收尾
# ---------------------------------------------------------------------------


def test_aborting_turn_stays_in_progress_until_done(monkeypatch):
    """中止信号已发出、done 尚未到达：本轮正文还没落库，必须仍算进行中。

    若此处判 idle，客户端对账会立即清掉叠加层正文，等 runner 落完 partial
    再发 done 才重新出现（"点停止后答案先消失再出现"）。rc.active 恰好在
    落库完成、done 发出前才翻转。
    """
    tree = _tree(CID, [_user("u1", 1), _assistant("a1", 5, "半截答案")])
    _patch_verify(monkeypatch, tree)

    async def run():
        sq = SessionQueue()
        _activate(sq, CID)
        sq.get_run_control(CID).stop()  # 用户点停止：abort_event 置位，active 仍为 True
        res = await _sync(sq)
        assert res["run_state"] == "active"
        assert res["messages"][-1]["in_progress"] is True

    _run(run())


def test_projection_activity_does_not_block_settlement(monkeypatch):
    """done 之后后台子代理可能仍活跃；消息面只看 RunControl.active。

    否则这条 assistant 消息会一直被标 in_progress，客户端要等子代理结束
    才收尾（表现为"回答完成但一直转圈"）。
    """
    tree = _tree(CID, [_user("u1", 1), _assistant("a1", 2, "答")])
    _patch_verify(monkeypatch, tree)

    async def run():
        sq = SessionQueue()
        projection = sq.get_or_create_web_runtime_projection(CID)
        projection.tool_calls = [{"id": "t1", "status": "running"}]
        res = await _sync(sq)
        assert res["run_state"] == "idle"
        assert res["messages"][-1]["in_progress"] is False
        assert res["run"] is None

    _run(run())


# ---------------------------------------------------------------------------
# 9. 非 owner worker：本地无运行态时用 Redis presence 判定
# ---------------------------------------------------------------------------


def test_presence_makes_non_owner_worker_report_active(monkeypatch):
    """run 在别的 worker 上：本地 is_turn_running 恒 False，不能据此 seal。"""
    tree = _tree(CID, [_user("u1", 1), _assistant("a1", 5, "别的 worker 上的半截答案")])
    _patch_verify(monkeypatch, tree)

    async def run():
        res = await sync_conversation(
            _fake_request(SessionQueue(), _FakePresence({CID: "active"})),
            session_id=CID,
            workspace_id="default",
            after_seq=0,
            user_id="admin",
        )
        assert res["run_state"] == "active"
        assert res["messages"][-1]["in_progress"] is True
        # 投影不在本进程：run 为 null，客户端保留既有叠加层即可。
        assert res["run"] is None

    _run(run())


def test_presence_unreadable_reports_unknown(monkeypatch):
    tree = _tree(CID, [_user("u1", 1), _assistant("a1", 2, "答")])
    _patch_verify(monkeypatch, tree)

    async def run():
        res = await sync_conversation(
            _fake_request(SessionQueue(), _FakePresence({CID: "unknown"})),
            session_id=CID,
            workspace_id="default",
            after_seq=0,
            user_id="admin",
        )
        assert res["run_state"] == "unknown"
        assert res["messages"][-1]["in_progress"] is True

    _run(run())


# ---------------------------------------------------------------------------
# 9. /messages 全量含 user；after_seq 与 in_progress 口径与 sync 一致
# ---------------------------------------------------------------------------


def test_messages_share_sync_semantics(monkeypatch):
    tree = _tree(
        CID,
        [_user("u1", 1), _assistant("a1", 2, "一"), _user("u2", 3), _assistant("a2", 6, "执行中…")],
    )
    _patch_verify(monkeypatch, tree)

    async def run():
        sq = SessionQueue()
        _activate(sq, CID)
        request = _fake_request(sq)

        sync_res = await sync_conversation(
            request,
            session_id=CID,
            workspace_id="default",
            after_seq=0,
            user_id="admin",
        )
        full = await get_messages(
            request,
            session_id=CID,
            offset=0,
            limit=50,
            user_id="admin",
        )
        # 全量历史与 sync 同口径：user/assistant 都按 seq 交付
        assert [m["id"] for m in full] == ["u1", "a1", "u2", "a2"]
        assert [m["id"] for m in sync_res["messages"]] == ["u1", "a1", "u2", "a2"]
        assert full[-1]["in_progress"] is True
        assert all(m["in_progress"] is False for m in full[:-1])

    _run(run())


def test_user_message_carries_client_message_id(monkeypatch):
    """user 消息进入 seq 体系，并携带客户端标识供本地气泡 re-key。"""
    tree = _tree(
        CID,
        [
            _user("u1", 1, client_message_id="usr_100"),
            _assistant("a1", 2, "答一"),
            _user("u2", 3, client_message_id="usr_200"),
        ],
    )
    _patch_verify(monkeypatch, tree)

    async def run():
        sq = SessionQueue()
        request = _fake_request(sq)

        sync_res = await sync_conversation(
            request,
            session_id=CID,
            workspace_id="default",
            after_seq=0,
            user_id="admin",
        )
        full = await get_messages(
            request,
            session_id=CID,
            offset=0,
            limit=50,
            user_id="admin",
        )
        for payload in (sync_res["messages"], full):
            users = {m["id"]: m for m in payload if m["role"] == "user"}
            assert users["u1"]["client_message_id"] == "usr_100"
            assert users["u2"]["client_message_id"] == "usr_200"
            # user 与 assistant 共用同一套 seq，客户端据此排序
            assert users["u1"]["seq"] == 1 and users["u2"]["seq"] == 3

    _run(run())


def test_user_event_roundtrip_keeps_client_message_id():
    """USER_MESSAGE 事件落库/回放后 client_message_id 不丢失。"""
    from prismclaw.schema.session.event_logger import SessionEventLogger
    from prismclaw.schema.session.types import _event_to_message_entry

    entry = _user("u1", 1, client_message_id="usr_100")
    event = SessionEventLogger().message_entry(CID, entry, entry.id)[0]
    assert event.data["client_message_id"] == "usr_100"

    restored = _event_to_message_entry(event.data, role="user", seq=2)
    assert restored is not None
    assert restored.client_message_id == "usr_100"


# ---------------------------------------------------------------------------
# 9. 新一轮刚发出、assistant 尚未落库：上一轮答案不得被误标 in_progress
# ---------------------------------------------------------------------------


def test_trailing_user_message_does_not_flag_previous_answer(monkeypatch):
    tree = _tree(CID, [_user("u1", 1), _assistant("a1", 2, "上一轮答案"), _user("u2", 3)])
    _patch_verify(monkeypatch, tree)

    async def run():
        sq = SessionQueue()
        _activate(sq, CID)
        res = await _sync(sq)
        assert [m["id"] for m in res["messages"]] == ["u1", "a1", "u2"]
        assert all(m["in_progress"] is False for m in res["messages"])
        assert res["settled_seq"] == 3

    _run(run())


# ---------------------------------------------------------------------------
# 10. steering：树结构无法区分"新轮次首条 user"，选择少标而不是误标
# ---------------------------------------------------------------------------


def test_steering_message_does_not_hide_previous_answer(monkeypatch):
    """两条 user 消息时，前一条 assistant 不标 in_progress。

    收益是已定稿答案绝不被藏掉；客户端按 seq 排序并以 client_message_id
    re-key 乐观气泡，不会因两条 user 并存而重复渲染。
    """

    tree = _tree(
        CID,
        [
            _user("u1", 1),
            _assistant("a1", 5, "执行中…"),
            _user("u2", 6, "方向调整"),
        ],
    )
    _patch_verify(monkeypatch, tree)

    async def run():
        sq = SessionQueue()
        _activate(sq, CID)
        res = await _sync(sq)
        assert [m["id"] for m in res["messages"]] == ["u1", "a1", "u2"]
        assert res["messages"][-1]["in_progress"] is False
        assert res["settled_seq"] == 6

    _run(run())


# ---------------------------------------------------------------------------
# 11. 正文为空但有执行时间线：消息必须保留（否则刷新后执行过程永久消失）
# ---------------------------------------------------------------------------


def test_timeline_only_turn_is_returned(monkeypatch):
    tree = _tree(
        CID,
        [_user("u1", 1), _assistant("a1", 2, ""), _timeline("c1", "a1", 3)],
    )
    _patch_verify(monkeypatch, tree)

    async def run():
        res = await _sync(SessionQueue())
        assert [m["id"] for m in res["messages"]] == ["u1", "a1"]
        msg = res["messages"][-1]
        assert msg["content"] == ""
        assert msg["timeline"]
        assert msg["seq"] == 3

    _run(run())
