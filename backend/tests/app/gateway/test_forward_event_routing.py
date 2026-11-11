"""阶段 6 回归护栏：跨节点事件可达性 + 既有链路不被破坏。

钉住六条最容易被「顺手改回去」的契约：
1. 转发线格式由 event_codec 单一真源约束，坏消息直接丢弃；
2. factory 的本地派发直投 ws.send_json（不再经过 send_event，避免两节点乒乓）；
3. web_deps 在插件重载后重建，派发必须按投递时刻解析 registry；
4. 本机有连接时绝不做 Redis 路由（WS 握手 SERVER_READY 不能被转走）；
5. 本机无连接时只转发一次，且 D5 契约不变（sent 恒 True）；
6. 原 socket 死亡后，新注册的 peer 下一帧立即收到投递——不存在任何
   冷却/拦帧窗口（旧实现的冷却检查位于 ws_dead 快路径之前，死 socket
   后 5s 内非 force 帧全被丢弃，形成「重连后投递黑洞」；冷却机制已删除）。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from prismclaw.app.gateway.event_codec import (
    decode_forward_event,
    encode_forward_event,
    web_session_key,
)
from prismclaw.app.gateway.factory import build_gateway_components
from prismclaw.app.gateway.router import GatewayRouter
from prismclaw.app.web.connection import (
    ConnectionRegistry,
    ConnectionState,
    _fanout_to_peer_connections,
    send_event,
)
from prismclaw.app.web.websocket.websocket import broadcast_to_user

_BACKEND = Path(__file__).resolve().parents[3]
_CONNECTION_SRC = _BACKEND / "src/prismclaw/app/web/connection.py"

_ENVELOPE = {
    "type": "chat.delta",
    "conversation_id": "c1",
    "seq": 42,
    "ts": 1000,
    "data": {"i": 1},
}


class _Peer:
    def __init__(self) -> None:
        self.frames: list[dict] = []

    async def send_json(self, frame: dict) -> None:
        self.frames.append(frame)


class _Router:
    def __init__(self, result: str = "forwarded") -> None:
        self.calls: list[tuple[str, dict]] = []
        self.result = result

    async def route(self, session_key: str, payload: dict) -> str:
        self.calls.append((session_key, payload))
        return self.result


def _app(registry: ConnectionRegistry, router: object) -> SimpleNamespace:
    return SimpleNamespace(
        state=SimpleNamespace(
            web_deps=SimpleNamespace(
                connection_registry=registry,
                gateway_router=router,
            )
        )
    )


def _ws(app: object) -> _Peer:
    ws = _Peer()
    ws.app = app  # type: ignore[attr-defined]
    return ws


def _state(ws: _Peer, user_id: str = "u1") -> ConnectionState:
    return ConnectionState(ws=ws, ws_session_id="probe", user_id=user_id)


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        affinity=SimpleNamespace(
            ttl_seconds=300, forward_prefix="prismclaw:forward:"
        )
    )


# ── 1. wire format ──────────────────────────────────────────────────────


def test_codec_roundtrip_and_session_key() -> None:
    payload = encode_forward_event("u1", _ENVELOPE)
    assert web_session_key("u1") == "web:u1"
    assert payload["session_key"] == "web:u1"
    assert decode_forward_event(payload) == ("u1", _ENVELOPE)


@pytest.mark.parametrize(
    "bad",
    [None, {}, {"user_id": "u1"}, {"frame": _ENVELOPE},
     {"user_id": "", "frame": {}}, {"user_id": "u1", "frame": "nope"}],
)
def test_codec_rejects_malformed_payload(bad: object) -> None:
    assert decode_forward_event(bad) is None


# ── 2/3. factory local dispatcher ───────────────────────────────────────


async def test_forwarded_frame_is_delivered_locally_without_rewrapping() -> None:
    registry = ConnectionRegistry()
    peer = _Peer()
    registry.connect("u1", peer)
    app = _app(registry, _Router())
    _router, consumer = build_gateway_components(
        object(), "worker-A", _settings(), app=app
    )
    handler = consumer._handler

    await handler(encode_forward_event("u1", _ENVELOPE))

    # Direct ws.send_json: seq/type untouched. A trip through send_event would
    # rebuild the envelope (seq=1) and could re-enter routing (ping-pong).
    assert peer.frames == [_ENVELOPE]


async def test_forwarded_malformed_payload_is_dropped() -> None:
    registry = ConnectionRegistry()
    peer = _Peer()
    registry.connect("u1", peer)
    app = _app(registry, _Router())
    _router, consumer = build_gateway_components(
        object(), "worker-A", _settings(), app=app
    )
    await consumer._handler({"user_id": "u1"})
    assert peer.frames == []


async def test_dispatcher_uses_registry_from_current_web_deps() -> None:
    """插件重载会重建 web_deps；捕获启动时的 registry 会投递到废弃对象。"""
    old_registry = ConnectionRegistry()
    old_peer = _Peer()
    old_registry.connect("u1", old_peer)
    app = _app(old_registry, _Router())
    _router, consumer = build_gateway_components(
        object(), "worker-A", _settings(), app=app
    )

    new_registry = ConnectionRegistry()
    new_peer = _Peer()
    new_registry.connect("u1", new_peer)
    app.state.web_deps = SimpleNamespace(
        connection_registry=new_registry, gateway_router=_Router()
    )

    await consumer._handler(encode_forward_event("u1", _ENVELOPE))
    assert old_peer.frames == []
    assert new_peer.frames == [_ENVELOPE]


# ── 4. local-first: handshake invariant ─────────────────────────────────


async def test_live_socket_never_routes_and_never_forwards() -> None:
    """SERVER_READY 走本地 socket；若先路由，多节点握手帧会被转给旧 owner。"""
    registry = ConnectionRegistry()
    router = _Router()
    app = _app(registry, router)
    ws = _ws(app)
    state = _state(ws)

    seq, sent = await send_event(state, "server.ready", "", {"x": 1})

    assert (seq, sent) == (1, True)
    assert [f["type"] for f in ws.frames] == ["server.ready"]
    assert router.calls == []


async def test_local_peer_delivery_does_not_forward() -> None:
    registry = ConnectionRegistry()
    peer = _Peer()
    registry.connect("u1", peer)
    router = _Router()
    state = _state(_ws(_app(registry, router)))

    delivered = await _fanout_to_peer_connections(state, dict(_ENVELOPE))

    assert delivered is True
    assert peer.frames == [_ENVELOPE]
    assert router.calls == []


# ── 5. no local peer: forward once, D5 intact ───────────────────────────


async def test_virtual_socket_forwards_once_and_still_reports_sent() -> None:
    """定时任务在节点 B、UI 在节点 A：帧转发一次，且 sent 恒 True。"""
    registry = ConnectionRegistry()
    router = _Router()
    app = _app(registry, router)
    state = _state(_ws(app))

    async def virtual_send(envelope: dict) -> None:
        await _fanout_to_peer_connections(state, envelope)

    state.ws.send_json = virtual_send  # type: ignore[method-assign]

    for event_type in ("chat.delta", "task.timeline", "chat.done"):
        _seq, sent = await send_event(state, event_type, "c1", {"x": 1})
        # D5 独立护栏：虚拟 socket 恒 True，与转发逻辑刻意解耦（不因 route
        # 失败变成 WS 故障）。本用例捕获退化的主断言是下方 router.calls。
        assert sent is True

    assert len(router.calls) == 3
    assert all(call[0] == "web:u1" for call in router.calls)
    assert [call[1]["frame"]["type"] for call in router.calls] == [
        "chat.delta", "task.timeline", "chat.done",
    ]
    assert decode_forward_event(router.calls[0][1])[0] == "u1"
    assert state.ws_dead is False


async def test_owner_is_local_and_offline_does_not_break_d5() -> None:
    router = _Router(result="local")
    state = _state(_ws(_app(ConnectionRegistry(), router)))

    async def virtual_send(envelope: dict) -> None:
        await _fanout_to_peer_connections(state, envelope)

    state.ws.send_json = virtual_send  # type: ignore[method-assign]
    _seq, sent = await send_event(state, "chat.done", "c1", {})
    assert sent is True  # D5：owner 是本节点且用户离线，也不能判成 WS 故障
    assert router.calls[0][0] == "web:u1"


async def test_no_router_keeps_legacy_silent_behaviour() -> None:
    state = _state(_ws(_app(ConnectionRegistry(), None)))
    assert await _fanout_to_peer_connections(state, dict(_ENVELOPE)) is False


# ── broadcast_to_user: scheduled_task.* ─────────────────────────────────


async def test_broadcast_forwards_scheduled_task_event_when_no_local_ui() -> None:
    router = _Router()
    app = _app(ConnectionRegistry(), router)

    sent = await broadcast_to_user(
        app, "u1", "scheduled_task.started", {"task_id": "t1"}
    )

    assert sent == 0  # 仅统计本机投递
    assert len(router.calls) == 1
    session_key, payload = router.calls[0]
    assert session_key == "web:u1"
    user_id, frame = decode_forward_event(payload)
    assert user_id == "u1"
    assert frame["type"] == "scheduled_task.started"
    assert frame["conversation_id"] is None
    assert frame["seq"] == 0
    assert frame["data"] == {"task_id": "t1"}


async def test_broadcast_with_local_ui_does_not_forward() -> None:
    registry = ConnectionRegistry()
    peer = _Peer()
    registry.connect("u1", peer)
    router = _Router()
    app = _app(registry, router)

    sent = await broadcast_to_user(
        app, "u1", "scheduled_task.completed", {"task_id": "t1"}
    )

    assert sent == 1
    assert peer.frames[0]["type"] == "scheduled_task.completed"
    assert router.calls == []


# ── router fallback must never break send_event ──────────────────────────


async def test_router_falls_back_to_local_on_any_redis_error() -> None:
    class _BoomAffinity:
        async def resolve(self, _key: str) -> None:
            raise RuntimeError("redis.exceptions.ConnectionError")

    router = GatewayRouter(_BoomAffinity(), object())
    assert await router.route("web:u1", {}) == "local"


async def test_router_does_not_swallow_cancellation() -> None:
    class _CancelAffinity:
        async def resolve(self, _key: str) -> None:
            raise asyncio.CancelledError()

    router = GatewayRouter(_CancelAffinity(), object())
    with pytest.raises(asyncio.CancelledError):
        await router.route("web:u1", {})


# ── assembly wiring ─────────────────────────────────────────────────────


async def test_lifecycle_passes_app_to_gateway_factory(monkeypatch) -> None:
    """factory 需要 app 才能在投递时解析（可重建的）web_deps。"""
    from prismclaw.assembly.bundles import lifecycle

    async def _no_curator(**_kwargs: object) -> None:
        return None

    monkeypatch.setattr(
        "prismclaw.runtime.curator_starter.start_curator_if_enabled",
        _no_curator,
    )

    captured: dict = {}

    def factory(redis_client, worker_id, settings, *, app):
        captured["app"] = app
        captured["worker_id"] = worker_id
        return None, None

    class _TaskManager:
        def spawn(self, *_args: object, **_kwargs: object) -> str:
            return "task"

    app = SimpleNamespace(state=SimpleNamespace())
    settings = SimpleNamespace(
        affinity=SimpleNamespace(
            enabled=True, heartbeat_interval=10, stale_threshold=30
        )
    )
    await lifecycle.build_app_lifecycle_bundle(
        app,
        settings=settings,
        redis_client=object(),
        worker_id="w1",
        task_manager=_TaskManager(),
        runner_deps=None,
        lock_manager=None,
        workspace_base=None,
        memory_store=None,
        gateway_factory=factory,
    )
    assert captured["app"] is app
    assert captured["worker_id"] == "w1"


# ── 结构性护栏：防止「转发逻辑被删」或「route 被搬回热路径」 ─────────────


def test_routing_lives_in_fanout_and_never_in_send_event_hot_path() -> None:
    """删掉转发、或把 route 搬回 send_event 的每个事件之前，都必须失败。"""
    src = _CONNECTION_SRC.read_text(encoding="utf-8")
    send_body = src.split("async def send_event(")[1].split("\nasync def ")[0]
    fanout_body = src.split("async def _fanout_to_peer_connections(")[1].split(
        "\nasync def "
    )[0]
    assert "router.route(" in fanout_body
    assert "router.route(" not in send_body
    assert "if delivered:" in fanout_body  # 本地优先不可退化为无条件转发
    # 冷却机制结构性禁令：send_event 不得再引入任何拦帧冷却状态
    # （send_failed_at 已随冷却删除，恢复即「重连后投递黑洞」回归）。
    assert "send_failed_at" not in send_body


async def test_no_router_single_node_virtual_socket_keeps_d5() -> None:
    """单机（gateway_router=None）下虚拟 socket 仍恒 sent=True。"""
    state = _state(_ws(_app(ConnectionRegistry(), None)))

    async def virtual_send(envelope: dict) -> None:
        await _fanout_to_peer_connections(state, envelope)

    state.ws.send_json = virtual_send  # type: ignore[method-assign]
    for event_type in ("chat.delta", "chat.done"):
        _seq, sent = await send_event(state, event_type, "c1", {})
        assert sent is True
    assert state.ws_dead is False


# ── 7. dead-socket reconnect: no delivery gap ───────────────────────────


async def test_dead_socket_frame_reaches_new_peer_immediately() -> None:
    """黑洞回归锚：原 socket 死亡后，新注册的 peer 下一帧立即收到投递。

    旧实现的冷却检查（send 失败后 5s 拦帧）位于 ws_dead 快路径之前：
    socket 死亡后窗口内非 force 帧全被丢弃，即使活 peer 已注册（「重连后
    投递黑洞」）。若有人原样恢复冷却块，本用例的第二帧断言立即失败。
    """
    registry = ConnectionRegistry()
    router = _Router(result="local")  # 无本地 peer 且不跨节点：用户彻底离线
    state = _state(_ws(_app(registry, router)))

    async def dead(_envelope: dict) -> None:
        raise RuntimeError("socket intentionally dead")

    state.ws.send_json = dead  # type: ignore[method-assign]

    _seq, sent = await send_event(state, "chat.delta", "c1", {"x": 1})
    assert sent is False  # 死 socket + 当时用户离线（fanout 无处投递）
    assert state.ws_dead is True

    # 用户重连：新 peer 注册（原 socket 已死，fanout 自动排除它）
    peer = _Peer()
    registry.connect("u1", peer)

    # 下一帧必须立即送达新 peer——不存在冷却窗口
    _seq, sent = await send_event(state, "chat.delta", "c1", {"x": 2})
    assert sent is True
    assert [f["data"] for f in peer.frames] == [{"x": 2}]
