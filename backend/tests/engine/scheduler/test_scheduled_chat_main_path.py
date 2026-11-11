"""定时任务 = 普通 chat turn 的回归测试。

把此前靠临时探针验证、随清理删除的验收固化成仓库内可重复运行的用例。
覆盖四条最容易静默回归的契约：

1. `ScheduledRunExecutor` 只做调度状态机，执行完全交给注入的 `run_chat`；
   传给它的是**纯任务描述**和已创建会话的 `session_id`，不是自研 agent loop。
2. 虚拟 socket 在「界面不在」时必须算**发送成功**：否则 `send_event` 会把
   没有 UI 判成 WS 故障（ws_dead 置位后 fanout 也失败），触发 `chat.done`
   的 8 次退避（约 31.5s）；
   `_emit_terminal_done` 排在 timeline / 交付物落库之前，收尾会被整体拖慢。
3. 调度层不设执行超时、不设租约：`run_chat` 完整执行且不被取消。
4. 会话 ID 必须满足 `_run_chat` 的 `web:{user_id}:` 前缀与 workspace 一致性校验。

测试通过显式 DI 测试桩注入确定性状态，不为测试修改任何生产代码。
装配函数 `_run_scheduled_chat` 定义在 `_lifespan` 闭包内部、无法直接 import，
因此用 AST 从**真实源码**抽出执行（而不是手写复刻），源码改动会立刻被测试感知。
"""
from __future__ import annotations

import ast
import asyncio
import importlib
import textwrap
from pathlib import Path
from types import SimpleNamespace

from prismclaw.app.web.connection import (
    ConnectionRegistry,
    ConnectionState,
    send_event,
)
from prismclaw.engine.scheduler.executor import ScheduledRunExecutor
from prismclaw.persistence.session.base import _generate_session_id
from prismclaw.schema.scheduler.scheduler import SchedulerDispatchJob
from prismclaw.schema.session.ids import build_web_session_key

_chat_runner = importlib.import_module("prismclaw.app.web.chat.chat_runner")

_LIFESPAN_SRC = (
    Path(__file__).resolve().parents[3] / "src/prismclaw/app/_lifespan.py"
)
_CONV_ID = "web:u1:default:s:abc123"
_INSTRUCTION = "整理今日AI新闻"


def _job() -> SchedulerDispatchJob:
    return SchedulerDispatchJob(
        run_id="r1",
        task_id="t1",
        task_name="每日AI新闻整理",
        instruction=_INSTRUCTION,
        user_id="u1",
        workspace_id="default",
        fired_at=0.0,
    )


class _FakeSessionStore:
    """用真实的 ID 生成规则造会话，避免测试替身悄悄掩盖契约漂移。"""

    async def create_new_session(self, *, session_key, workspace_id, agent_id, title):
        return SimpleNamespace(header=SimpleNamespace(id=_generate_session_id(session_key)))


class _FakeStore:
    def __init__(self, *, consecutive_failures: int = 0) -> None:
        self.runs: dict[str, dict] = {}
        self.statuses: list[tuple[str, str, dict]] = []
        self.conversations: list[tuple[str, str]] = []
        self.notifications: list[tuple[str, dict]] = []
        self.task = SimpleNamespace(
            consecutive_failures=consecutive_failures, enabled=True
        )

    async def update_run(self, run_id: str, **kw) -> None:
        self.runs.setdefault(run_id, {}).update(kw)

    async def update_status(self, task_id: str, status: str, **kw) -> None:
        self.statuses.append((task_id, status, kw))

    async def get_task(self, task_id: str):
        return self.task

    async def put_task(self, task) -> None:
        self.task = task

    async def set_conversation(self, task_id: str, session_id: str) -> None:
        self.conversations.append((task_id, session_id))


def _make_executor(
    run_chat,
    store: _FakeStore,
    *,
    max_consecutive_failures: int = 3,
) -> ScheduledRunExecutor:
    app = SimpleNamespace(
        state=SimpleNamespace(
            runner_deps=SimpleNamespace(session_store=_FakeSessionStore()),
            redis_client=None,
        )
    )

    async def broadcast(user_id: str, event_type: str, data: dict) -> None:
        store.notifications.append((event_type, data))

    settings = SimpleNamespace(
        max_consecutive_failures=max_consecutive_failures,
    )
    return ScheduledRunExecutor(
        app=app,
        store=store,
        settings=settings,
        broadcast=broadcast,
        run_chat=run_chat,
    )


# ── 调度状态机 ────────────────────────────────────────────────────────────


async def test_executor_delegates_plain_instruction_to_injected_run_chat():
    """执行链路是注入的普通 chat turn，不是 executor 自研的 agent loop。"""
    seen: dict = {}

    async def run_chat(**kw) -> None:
        seen.update(kw)

    store = _FakeStore()
    await _make_executor(run_chat, store).execute(_job())

    assert set(seen) == {"user_id", "conversation_id", "content", "workspace_id"}
    assert seen["user_id"] == "u1"
    assert seen["workspace_id"] == "default"
    assert seen["content"] == _INSTRUCTION  # 无交付物前缀 / 综合轮指令注入
    session_id = seen["conversation_id"]
    assert session_id.startswith("web:u1:default:s:")

    assert store.runs["r1"]["status"] == "success"
    assert store.runs["r1"]["session_id"] == session_id
    assert store.conversations == [("t1", session_id)]
    assert [evt for evt, _ in store.notifications] == [
        "scheduled_task.started",
        "scheduled_task.completed",
    ]
    assert store.notifications[-1][1]["status"] == "success"


async def test_executor_does_not_cut_off_a_long_running_chat():
    """调度层不设执行超时：run_chat 完整执行且不被取消。"""
    finished = False

    async def slow(**_kw) -> None:
        nonlocal finished
        await asyncio.sleep(0.03)
        finished = True

    store = _FakeStore()
    await _make_executor(slow, store).execute(_job())

    assert finished is True
    assert store.runs["r1"]["status"] == "success"
    assert store.notifications[-1][1]["status"] == "success"


async def test_executor_marks_failure_and_disables_after_consecutive_limit():
    async def boom(**_kw) -> None:
        raise RuntimeError("boom")

    store = _FakeStore(consecutive_failures=2)
    await _make_executor(boom, store, max_consecutive_failures=3).execute(_job())

    assert store.runs["r1"]["status"] == "failed"
    assert any(status == "disabled" for _, status, _ in store.statuses)
    assert any(
        evt == "scheduled_task.disabled" for evt, _ in store.notifications
    )


def test_created_session_id_satisfies_web_chat_contract():
    """_run_chat 要求 web:{user_id}: 前缀；build_web_session_key 必须带 workspace。"""
    key = build_web_session_key("u1", "default")
    assert key == "web:u1:default"
    session_id = _generate_session_id(key)
    assert session_id.startswith("web:u1:")  # _run_chat 的 expected_prefix
    assert session_id.startswith(f"{key}:s:")


# ── 虚拟 socket（真实装配代码）────────────────────────────────────────────


class _Peer:
    def __init__(self) -> None:
        self.frames: list[dict] = []

    async def send_json(self, envelope: dict) -> None:
        self.frames.append(envelope)


def _load_real_scheduled_chat():
    source = _LIFESPAN_SRC.read_text(encoding="utf-8")
    tree = ast.parse(source)
    nodes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "_run_scheduled_chat"
    ]
    assert len(nodes) == 1, (
        f"{_LIFESPAN_SRC}: expected exactly one _run_scheduled_chat, "
        f"found {len(nodes)} — assembly shape changed, update this test"
    )
    snippet = textwrap.dedent(ast.get_source_segment(source, nodes[0]))
    namespace: dict = {}
    exec(compile(ast.parse(snippet), str(_LIFESPAN_SRC), "exec"), namespace)  # noqa: S102
    return namespace["_run_scheduled_chat"]


async def _drive_virtual_socket(monkeypatch, peers_before, peers_after=None):
    """驱动真实的 _run_scheduled_chat，并用真实 send_event 发 4 帧 + 1 个 done。

    peers_after 用于模拟「执行中途用户打开界面」。
    """
    registry = ConnectionRegistry()
    for peer in peers_before:
        registry.connect("u1", peer)

    captured: dict = {}
    sent: list[bool] = []

    async def fake_run_chat(state, msg, settings) -> None:
        captured.update(state=state, msg=msg, settings=settings)
        for i in range(2):
            _seq, ok = await send_event(
                state, "chat.delta", msg.conversation_id, {"i": i}
            )
            sent.append(ok)
        if peers_after:
            for peer in peers_after:
                registry.connect("u1", peer)
        for i in range(2, 4):
            _seq, ok = await send_event(
                state, "chat.delta", msg.conversation_id, {"i": i}
            )
            sent.append(ok)
        _seq, ok = await send_event(
            state,
            "chat.done",
            msg.conversation_id,
            {"final_message": "x"},
        )
        sent.append(ok)

    monkeypatch.setattr(_chat_runner, "_run_chat", fake_run_chat)
    app = SimpleNamespace(
        state=SimpleNamespace(
            web_deps=SimpleNamespace(
                connection_registry=registry,
                web_settings=SimpleNamespace(default_mode="work"),
            )
        )
    )
    fn = _load_real_scheduled_chat()
    fn.__globals__["app"] = app
    await fn(
        user_id="u1",
        conversation_id=_CONV_ID,
        content=_INSTRUCTION,
        workspace_id="default",
    )
    all_peers = list(peers_before) + list(peers_after or [])
    return captured, sent, all_peers


async def test_virtual_socket_is_silent_but_never_fails_when_ui_closed(monkeypatch):
    """界面不在：静默，但绝不能被判成 WS 故障（否则触发 31.5s 退避）。"""
    captured, sent, peers = await _drive_virtual_socket(monkeypatch, [])

    assert sent == [True] * 5
    assert peers == []
    state = captured["state"]
    assert state.ws_dead is False
    assert captured["msg"].content == _INSTRUCTION
    assert captured["msg"].conversation_id == _CONV_ID
    assert captured["settings"] is not None


async def test_virtual_socket_fanouts_every_frame_when_ui_open(monkeypatch):
    """界面在：所有 chat/task 事件实时投递，且 conversation_id 正确路由。"""
    peer = _Peer()
    captured, sent, _ = await _drive_virtual_socket(monkeypatch, [peer])

    assert sent == [True] * 5
    assert [frame["type"] for frame in peer.frames] == [
        "chat.delta",
        "chat.delta",
        "chat.delta",
        "chat.delta",
        "chat.done",
    ]
    assert all(frame["conversation_id"] == _CONV_ID for frame in peer.frames)


async def test_virtual_socket_delivers_immediately_after_ui_opens_mid_run(monkeypatch):
    """回归护栏：旧设计在 5s 冷却期内会丢掉打开界面后的所有帧（0 帧）。"""
    peer = _Peer()
    captured, sent, _ = await _drive_virtual_socket(monkeypatch, [], [peer])

    assert sent == [True] * 5
    assert len(peer.frames) == 3  # 打开之后：2 帧 delta + 1 帧 done


async def test_dead_virtual_socket_would_be_treated_as_ws_failure():
    """回归护栏：证明虚拟 socket 不能用 ws_dead=True + 抛异常的死 socket。

    一旦有人把装配改回旧写法，上面的三条用例会立刻失败，这条解释了原因。
    ws_dead=True 且无 peer 时 send_event 返回 False——这正是调用方
    （chat_runner._emit_terminal_done）进入终态退避重试的信号。
    """
    registry = ConnectionRegistry()

    async def dead(_envelope: dict) -> None:
        raise RuntimeError("virtual socket intentionally dead")

    state = ConnectionState(
        ws=SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    web_deps=SimpleNamespace(connection_registry=registry)
                )
            ),
            send_json=dead,
        ),
        ws_session_id="scheduler:probe",
        user_id="u1",
        ws_dead=True,
    )
    _seq, ok = await send_event(state, "chat.done", _CONV_ID, {})
    assert ok is False
