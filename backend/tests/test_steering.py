"""steering 通道（排队消息提升注入）单测——SDD sdd-steering-channel.md 第 8 节。

覆盖 10 组：
1-2. mark_steered 四分支 + 取消撤销提升（取消优先于提升）
3.   consumer 取出分流：转移（steered）/ 跳过（cancelled）/ 执行（无标记）
4.   consumer finally 兜底：未消费 steering 转回排队（G4）
5.   settle_steering_after_run：未转移标记失效 + 已转移转回排队
6.   cap：steering 通道占用满 10 拒绝第 11 条
7.   无 client_message_id 回归：消息照常执行，不受 steering 影响
8.   FIFO：多条 steering 按标记顺序保序注入
9.   run_loop 骨架集成：消费点清 override（G5）
10.  协议层：chat.steer / chat.cancel 解析 + WS handler 注册接线
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

from prismclaw.app.web.chat.chat_protocol import (
    CANCEL_OK,
    STEER_ALREADY,
    STEER_CAP_REACHED,
    STEER_NOT_FOUND,
    STEER_OK,
    SessionQueue,
    _STEERING_CAP,
)
from prismclaw.app.web.protocol.messages import (
    ChatCancelMessage,
    ChatSendMessage,
    ChatSteerMessage,
    SERVER_CHAT_STEERING_ACK,
    _CLIENT_MESSAGE_MAP,
    parse_client_message,
)
from prismclaw.engine.execution.checkpoint.models import RunState
from prismclaw.engine.execution.control.kernel import Decision
from prismclaw.engine.execution.loop.agent_loop import run_loop
from prismclaw.engine.execution.loop.types import (
    ExecutePort,
    LoopConfig,
    PreparePort,
    RunLoopState,
    StreamPort,
)


def _msg(cid: str, mid: str | None, content: str | None = None) -> ChatSendMessage:
    return ChatSendMessage(
        conversation_id=cid,
        content=content if content is not None else f"msg-{mid}",
        client_message_id=mid,
    )


# ---------------------------------------------------------------------------
# 1-2. mark_steered / mark_cancelled 结果与优先级
# ---------------------------------------------------------------------------

def test_mark_steered_outcomes():
    async def run():
        sq = SessionQueue()
        cid = "c1"
        await sq.enqueue(cid, _msg(cid, "A"), _noop_handler)
        assert sq.mark_steered(cid, "A") == (STEER_OK, 1)
        # already：占席口径一致（已占 1 席，不是裸 deque 长度 0）
        assert sq.mark_steered(cid, "A") == (STEER_ALREADY, 1)
        assert sq.mark_steered(cid, "missing")[0] == STEER_NOT_FOUND
        assert sq.mark_steered(cid, "")[0] == STEER_NOT_FOUND
        # 空会话
        assert sq.mark_steered("no-such-cid", "A")[0] == STEER_NOT_FOUND

    asyncio.run(run())


def test_cancel_revokes_steer_priority():
    """取消已提升但未转移的消息：撤销提升标记——不注入刚被取消的内容。"""
    async def run():
        sq = SessionQueue()
        cid = "c1"
        await sq.enqueue(cid, _msg(cid, "A"), _noop_handler)
        assert sq.mark_steered(cid, "A")[0] == STEER_OK
        assert sq.mark_cancelled(cid, "A") == CANCEL_OK
        assert "A" not in sq._steered.get(cid, set())
        assert "A" in sq._cancelled.get(cid, set())
        assert sq.mark_cancelled(cid, "missing") == STEER_NOT_FOUND

    asyncio.run(run())


async def _noop_handler(msg: ChatSendMessage) -> None:
    pass


# ---------------------------------------------------------------------------
# 3. consumer 取出分流
# ---------------------------------------------------------------------------

def test_consumer_transfer_skip_execute():
    async def run():
        sq = SessionQueue()
        cid = "c1"
        handled: list[str] = []

        async def handler(m: ChatSendMessage) -> None:
            handled.append(str(m.client_message_id))

        for mid in ("A", "B", "C"):
            await sq.enqueue(cid, _msg(cid, mid), handler)
        assert sq.mark_steered(cid, "A")[0] == STEER_OK
        assert sq.mark_cancelled(cid, "C") == CANCEL_OK

        task = asyncio.create_task(sq._consume(cid))
        for _ in range(250):
            if handled:
                break
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.05)
        assert handled == ["B"], f"A 应转移、C 应跳过、仅 B 执行: {handled}"
        assert sq._steering_len(cid) == 1  # A 在 steering deque
        assert not sq._steered.get(cid, set())  # 标记已消费
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run())


def test_no_client_message_id_regression():
    """无 client_message_id 的消息：正常执行，不进任何 steering 分支。"""
    async def run():
        sq = SessionQueue()
        cid = "c1"
        handled: list[str] = []

        async def handler(m: ChatSendMessage) -> None:
            handled.append(str(m.client_message_id))

        await sq.enqueue(cid, _msg(cid, None, "plain message"), handler)
        task = asyncio.create_task(sq._consume(cid))
        for _ in range(250):
            if handled:
                break
            await asyncio.sleep(0.02)
        assert handled == ["None"]
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 4. consumer finally 兜底（G4）
# ---------------------------------------------------------------------------

def test_consumer_finally_drain_fallback():
    async def run():
        sq = SessionQueue()  # task_manager=None → 兜底不重新拉起 consumer
        cid = "c1"
        handled: list[str] = []

        async def handler(m: ChatSendMessage) -> None:
            handled.append(str(m.client_message_id))

        await sq.enqueue(cid, _msg(cid, "M1"), handler)
        assert sq.mark_steered(cid, "M1")[0] == STEER_OK
        task = asyncio.create_task(sq._consume(cid))
        for _ in range(250):
            if sq._steering_len(cid) == 1:
                break
            await asyncio.sleep(0.02)
        assert sq._steering_len(cid) == 1

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(0)

        # finally：先注销自身再 drain——消息转回排队、索引有效（可再操作）
        q = sq._queues.get(cid)
        assert q is not None and q.qsize() == 1, "drain 兜底丢失消息"
        assert q.get_nowait()[0].client_message_id == "M1"
        q.task_done()
        assert sq._steering.get(cid) is None
        assert sq._steered.get(cid) is None
        assert sq._cancelled.get(cid) is None

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 5. settle_steering_after_run（run 结束收口）
# ---------------------------------------------------------------------------

def test_settle_after_run():
    async def run():
        sq = SessionQueue()
        cid = "c1"
        handled: list[str] = []

        async def handler(m: ChatSendMessage) -> None:
            handled.append(str(m.client_message_id))

        await sq.enqueue(cid, _msg(cid, "X"), handler)
        await sq.enqueue(cid, _msg(cid, "Y"), handler)
        assert sq.mark_steered(cid, "X")[0] == STEER_OK  # 待转移（仍在排队）
        sq.push_steering(cid, _msg(cid, "Z"), handler)  # 已转移

        n = sq.settle_steering_after_run(cid)
        assert n == 1  # Z 转回排队
        assert not sq._steered.get(cid, set())  # X 待转移标记失效 → FIFO 原位执行
        q = sq._queues[cid]
        ids = []
        while not q.empty():
            ids.append(q.get_nowait()[0].client_message_id)
            q.task_done()
        assert ids == ["X", "Y", "Z"], ids

        # cancelled 不因 settle 复活（持久用户意图）
        await sq.enqueue(cid, _msg(cid, "K"), handler)
        assert sq.mark_cancelled(cid, "K") == CANCEL_OK
        sq.settle_steering_after_run(cid)
        assert "K" in sq._cancelled.get(cid, set())

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 6. cap
# ---------------------------------------------------------------------------

def test_steering_cap():
    async def run():
        sq = SessionQueue()
        cid = "c1"
        for i in range(_STEERING_CAP + 3):
            await sq.enqueue(cid, _msg(cid, f"M{i}"), _noop_handler)
        ok = 0
        for i in range(_STEERING_CAP + 3):
            code, n = sq.mark_steered(cid, f"M{i}")
            if code == STEER_OK:
                ok += 1
            if code == STEER_CAP_REACHED:
                assert n == _STEERING_CAP  # 拒绝时返回当前占用（满）
        assert ok == _STEERING_CAP
        code, n = sq.mark_steered(cid, "M-extra")
        assert code == STEER_NOT_FOUND  # 不存在的 id（cap 优先判定 not_found）
        # 已排队但未登记的新消息 → cap_reached
        await sq.enqueue(cid, _msg(cid, "late"), _noop_handler)
        assert sq.mark_steered(cid, "late")[0] == STEER_CAP_REACHED

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 8. FIFO 保序
# ---------------------------------------------------------------------------

def test_drain_fifo_order():
    async def run():
        sq = SessionQueue()
        cid = "c1"
        for mid in ("s1", "s2", "s3"):
            await sq.enqueue(cid, _msg(cid, mid), _noop_handler)
            assert sq.mark_steered(cid, mid)[0] == STEER_OK

        task = asyncio.create_task(sq._consume(cid))
        for _ in range(250):
            if sq._steering_len(cid) == 3:
                break
            await asyncio.sleep(0.02)
        # 引擎侧先取走（cancel 会触发 finally 兜底把消息转回排队——
        # 那是 test_consumer_finally_drain_fallback 覆盖的路径）。
        items = sq.drain_steering(cid)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert [m.client_message_id for m, _ in items] == ["s1", "s2", "s3"]

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 9. run_loop 骨架集成：steering 消费注入（G5）
# ---------------------------------------------------------------------------

def test_run_loop_steering_injection():
    async def run():
        state = RunLoopState(
            iteration=0,
            run_state=RunState(session_id="s1"),
        )
        calls = {"steering": 0, "llm": 0}

        async def get_steering_messages() -> list[str]:
            calls["steering"] += 1
            return ["用户中途纠偏：改用方案 B"] if calls["steering"] == 1 else []

        async def stream_llm(state: RunLoopState):
            calls["llm"] += 1
            state.llm_response = SimpleNamespace(tool_calls=[], finish_reason="stop")
            yield None  # 使函数成为 async generator（真实闭包总是 yield 流事件）

        async def should_continue(state, response=None, *, event) -> Decision:
            return Decision("COMPLETE", "test_done")  # 首个 preflight 即终止

        async def noop_prepare(state):
            return None

        async def noop_execute(state, response, allowed_calls):
            return
            yield  # pragma: no cover

        config = LoopConfig(
            prepare=PreparePort(prepare_next_turn=noop_prepare),
            stream=StreamPort(stream_llm=stream_llm),
            execute=ExecutePort(execute_tools=noop_execute),
            should_continue=should_continue,
            get_steering_messages=get_steering_messages,
        )
        events = []
        async for ev in run_loop(state, config):
            events.append(ev)

        assert calls["steering"] >= 1
        assert calls["llm"] == 1

    asyncio.run(run())


def test_run_loop_no_steering_callback_unchanged():
    """G3：未配置 get_steering_messages 时骨架行为不变（零开销路径）。"""
    async def run():
        state = RunLoopState(iteration=0, run_state=RunState(session_id="s1"))

        async def stream_llm(state: RunLoopState):
            state.llm_response = SimpleNamespace(tool_calls=[], finish_reason="stop")
            yield None  # async generator（真实闭包总是 yield 流事件）

        async def should_continue(state, response=None, *, event) -> Decision:
            return Decision("COMPLETE", "test_done")

        async def noop_prepare(state):
            return None

        async def noop_execute(state, response, allowed_calls):
            return
            yield  # pragma: no cover

        config = LoopConfig(
            prepare=PreparePort(prepare_next_turn=noop_prepare),
            stream=StreamPort(stream_llm=stream_llm),
            execute=ExecutePort(execute_tools=noop_execute),
            should_continue=should_continue,
        )
        events = []
        async for ev in run_loop(state, config):
            events.append(ev)

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 10. 协议层 + WS 接线
# ---------------------------------------------------------------------------

def test_protocol_parse_steer_and_cancel():
    m = parse_client_message(
        {"type": "chat.steer", "conversation_id": "c1", "client_message_id": "m-42"}
    )
    assert isinstance(m, ChatSteerMessage)
    assert m.conversation_id == "c1" and m.client_message_id == "m-42"

    m = parse_client_message(
        {"type": "chat.cancel", "conversation_id": "c1", "client_message_id": "m-43"}
    )
    assert isinstance(m, ChatCancelMessage)
    assert m.client_message_id == "m-43"

    assert SERVER_CHAT_STEERING_ACK == "chat.steering_ack"
    assert "chat.steer" in _CLIENT_MESSAGE_MAP
    assert "chat.cancel" in _CLIENT_MESSAGE_MAP


def test_websocket_handler_wiring():
    """WS 层接线（源码断言）：dispatch 分支 + handler 注册 + RunRequest 传递。"""
    ws_src = (
        Path(__file__).resolve().parents[1]
        / "src/prismclaw/app/web/websocket/websocket.py"
    ).read_text(encoding="utf-8")
    assert 'state.msg_handlers["chat.steer"] = _on_chat_steer' in ws_src
    assert 'state.msg_handlers["chat.cancel"] = _on_chat_cancel' in ws_src
    assert "isinstance(msg, ChatSteerMessage)" in ws_src
    assert "isinstance(msg, ChatCancelMessage)" in ws_src
    assert "session_queue.mark_steered(" in ws_src
    assert "session_queue.mark_cancelled(" in ws_src

    runner_src = (
        Path(__file__).resolve().parents[1]
        / "src/prismclaw/app/web/chat/chat_runner.py"
    ).read_text(encoding="utf-8")
    assert "conversation_id=msg.conversation_id," in runner_src
    assert "settle_steering_after_run" in runner_src


def test_engine_closure_source_wiring():
    """引擎闭包接线（源码断言）：LoopConfig 字段 + 骨架消费点 + 闭包实现。"""
    wiring_src = (
        Path(__file__).resolve().parents[1]
        / "src/prismclaw/engine/execution/loop/wiring.py"
    ).read_text(encoding="utf-8")
    assert "get_steering_messages=partial(_terminal.get_steering_messages, rt)" in wiring_src

    terminal_src = (
        Path(__file__).resolve().parents[1]
        / "src/prismclaw/engine/execution/loop/terminal.py"
    ).read_text(encoding="utf-8")
    assert 'role="user"' in terminal_src  # 落树原样 user 身份（无前缀）

    types_src = (
        Path(__file__).resolve().parents[1]
        / "src/prismclaw/engine/execution/loop/types.py"
    ).read_text(encoding="utf-8")
    assert "get_steering_messages: Callable[[], Awaitable[list[str]]] | None" in types_src

    req_src = (
        Path(__file__).resolve().parents[1]
        / "src/prismclaw/schema/agent/types.py"
    ).read_text(encoding="utf-8")
    assert "conversation_id: str | None = None" in req_src


# 保证 backend/src 在 path（conftest 已处理；独立运行兜底）
_PROJECT_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(_PROJECT_SRC))