"""WebSocket connection state and event sending utilities.

Extracted from :mod:`prismclaw.channels.web.websocket.websocket` to break
circular dependencies between the websocket and chat layers.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

from prismclaw.app.gateway.event_codec import (
    encode_forward_event,
    web_session_key,
)


logger = logging.getLogger(__name__)

# ── Message handler registry type ─────────────────────────────────────────
# Each handler receives (state, msg) and is awaited by the dispatch loop.
MsgHandler = Callable[..., Coroutine[Any, Any, None]]


@dataclass
class ConnectionState:
    ws: Any
    ws_session_id: str
    user_id: str | None = None
    seq: int = 0
    last_pong: float = field(default_factory=time.time)
    authenticated: bool = False
    # Once set True, state.ws is confirmed dead — skip it on every send
    # and route straight to the registry's live peer connections.  Never
    # resets: send_event treats the first failure as terminal for the
    # socket and relies on peer fanout / gateway routing from then on.
    ws_dead: bool = False
    # Step 3: handler registry — websocket dispatch loop calls these instead
    # of importing chat.chat_auth directly.
    msg_handlers: dict[str, MsgHandler] = field(default_factory=dict)
    # Session queue resolver — injected by chat layer to break protocol→chat dep.
    get_session_queue: Callable[..., Any] | None = None


class ConnectionRegistry:
    def __init__(self) -> None:
        self._connections: dict[str, set[Any]] = {}

    def connect(self, user_id: str, ws: Any) -> None:
        self._connections.setdefault(user_id, set()).add(ws)

    def disconnect(self, user_id: str, ws: Any) -> None:
        conns = self._connections.get(user_id)
        if conns is not None:
            conns.discard(ws)
            if not conns:
                del self._connections[user_id]

    def count(self, user_id: str) -> int:
        return len(self._connections.get(user_id, set()))

    def get_connections(self, user_id: str) -> set[Any]:
        return set(self._connections.get(user_id, set()))

    def clear(self) -> None:
        self._connections.clear()


def _resolve_connection_registry(state: ConnectionState) -> ConnectionRegistry | None:
    """Best-effort lookup of the app-wide registry owning ``state``'s socket.

    ``state.ws`` is a Starlette ``WebSocket`` whose ``.app``/``.scope`` stay
    valid after the socket closes, so a run that outlives its connection can
    still locate where the user's *other* connections are registered.
    """
    try:
        web_deps = state.ws.app.state.web_deps
    except Exception:  # noqa: BLE001 - 非 app 上下文（测试替身）无此结构
        return None
    registry = getattr(web_deps, "connection_registry", None)
    return registry if isinstance(registry, ConnectionRegistry) else None


def _resolve_gateway_router(state: ConnectionState) -> Any | None:
    """Best-effort lookup of this node's gateway router.

    ``web_deps`` is rebuilt on plugin reload, so the router is read at send
    time rather than captured when the connection was created.
    """
    try:
        web_deps = state.ws.app.state.web_deps
    except Exception:  # noqa: BLE001 - 非 app 上下文（测试替身）无此结构
        return None
    return getattr(web_deps, "gateway_router", None)


async def _fanout_to_peer_connections(
    state: ConnectionState,
    envelope: dict[str, Any],
) -> bool:
    """Deliver ``envelope`` to the same user's other live connections.

    Returns True when at least one peer accepted the frame.  This is the
    reconnect story for in-flight runs: a run captures the ``ConnectionState``
    of the socket that received ``chat.send``, and that socket can die while
    the run keeps going (page reload / HMR / network drop).  The user's fresh
    connection registers under the same ``user_id``, so events are rerouted to
    it instead of being silently dropped — previously the tail of the stream
    (``task.timeline``, ``chat.done``) was lost
    ("chat.done event permanently lost after 8 attempts").

    When no local connection accepts the frame, the user may be connected to
    a different worker. The frame is then forwarded once to the affinity
    owner; a scheduled run executing on node B can therefore stream into a UI
    connected to node A. Local delivery is always attempted first, which
    keeps the ordinary chat path free of Redis hops and keeps the WS
    handshake (``SERVER_READY``) on the socket that just connected.
    """
    if not state.user_id:
        return False
    registry = _resolve_connection_registry(state)
    delivered = False
    if registry is not None:
        for ws in registry.get_connections(state.user_id):
            if ws is state.ws:
                continue  # 已判定发送失败的原连接，不重试
            try:
                await ws.send_json(envelope)
                delivered = True
            except Exception:  # noqa: BLE001 - 单条连接失败不影响其它连接
                continue
    if delivered:
        return True

    router = _resolve_gateway_router(state)
    if router is None:
        return False
    result = await router.route(
        web_session_key(state.user_id),
        encode_forward_event(state.user_id, envelope),
    )
    return result == "forwarded"


async def send_event(
    state: ConnectionState,
    event_type: str,
    conversation_id: str,
    data: dict[str, Any],
) -> tuple[int, bool]:
    """Send an event over the WebSocket.

    Returns (seq, sent).  ``sent`` is False only when the originating socket
    is confirmed dead and no live peer connection — nor the gateway route to
    the affinity owner — accepted the frame, i.e. the user is currently
    unreachable.  A dead socket's frames reroute straight to the user's
    live peer connections (see :func:`_fanout_to_peer_connections`), so a
    fast reconnect keeps streaming without a delivery gap; a successful
    reroute counts as sent.

    历史注记：旧实现曾在 send 失败后用 5s 冷却拦帧——冷却检查位于 ws_dead
    快路径之前，死 socket 后的重连窗口内非终态帧全被丢弃（「重连后 5s
    投递黑洞」）；且冷却的防 CPU 目的（不反复重试死 socket）已由 ws_dead
    快路径独立承担，失败时间戳在 ws_dead=False 时不可达非零——冷却
    机制整体删除（消除黑洞，无未覆盖副作用；回归由 connection 层
    测试钉住）。
    """
    state.seq += 1
    envelope = {
        "type": event_type,
        "conversation_id": conversation_id,
        "seq": state.seq,
        "ts": int(time.time() * 1000),
        "data": data,
    }

    # Fast path: once state.ws is confirmed dead, skip it on every send
    # and route straight to the registry's live peer connections.
    if not state.ws_dead:
        try:
            await state.ws.send_json(envelope)
            return state.seq, True
        except Exception:  # noqa: BLE001 - 断开/运行时错误统一走重投递
            state.ws_dead = True
            logger.info(
                "ws send failed; marking state.ws dead; "
                "will reroute future events to live peer connections "
                "(ws_session_id=%s)",
                state.ws_session_id,
            )

    # state.ws is dead — fanout to the user's other live connections.
    # 每帧都尝试而非冷却限流：用户重连后下一帧立即送达（无投递黑洞）。
    # 无 peer 时的成本是每帧一次 registry 查询（本地 set）+ gateway 路由
    # （网络调用），由投递正确性优先的取向接受；run 终态帧由调用方的
    # 退避重试兜底（chat_runner._emit_terminal_done）。
    if await _fanout_to_peer_connections(state, envelope):
        return state.seq, True
    return state.seq, False
