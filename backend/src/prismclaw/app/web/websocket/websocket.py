from __future__ import annotations

import asyncio
import logging
import secrets
import time
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from prismclaw.app.auth.auth import verify_jwt
from prismclaw.app.web.connection import (  # noqa: F401 – re-exports
    ConnectionRegistry,
    ConnectionState,
    send_event,
)
from prismclaw.app.web.protocol.protocol import (
    SERVER_CHAT_STEERING_ACK,
    SERVER_ERROR,
    SERVER_HELLO,
    SERVER_PING,
    SERVER_READY,
    ChatAbortMessage,
    ChatCancelMessage,
    ChatSendMessage,
    ChatSteerMessage,
    IdentifyMessage,
    PongMessage,
    ToolClarifyMessage,
    parse_client_message,
)
from prismclaw.schema.protocol.protocol_types import PROTOCOL_VERSION, ProtocolErrorCode
from prismclaw.infra.settings import WebSettings
from prismclaw.infra.task_manager import TaskManager

logger = logging.getLogger(__name__)

ws_router = APIRouter()


registry = ConnectionRegistry()


def _get_connection_registry(websocket: WebSocket) -> ConnectionRegistry:
    from prismclaw.app.web.deps import WebDeps

    web_deps: WebDeps = websocket.app.state.web_deps
    return web_deps.connection_registry


async def broadcast_to_user(
    app: Any,
    user_id: str,
    event_type: str,
    data: dict[str, Any],
) -> int:
    """Broadcast an event to all active WebSocket connections of a user.

    Used by the scheduler system for task status notifications that are
    not tied to a specific conversation. When this node has no live socket
    for the user, the frame is forwarded once to the affinity owner so a
    scheduled run executing on node B still reaches a UI connected to A.

    The return value counts **local** deliveries only: a forwarded frame is
    best-effort and has no local recipient to count.
    """
    from prismclaw.app.gateway.event_codec import (
        encode_forward_event,
        web_session_key,
    )
    from prismclaw.app.web.deps import WebDeps

    web_deps: WebDeps = app.state.web_deps
    reg: ConnectionRegistry = web_deps.connection_registry
    frame = {
        "type": event_type,
        "conversation_id": None,
        "seq": 0,
        "ts": int(time.time() * 1000),
        "data": data,
    }
    sent = 0
    for ws in reg.get_connections(user_id):
        try:
            await ws.send_json(frame)
            sent += 1
        except Exception:  # noqa: BLE001 - 单条连接失败不影响其它连接
            continue
    if sent:
        return sent

    router = getattr(web_deps, "gateway_router", None)
    if router is None:
        return 0
    await router.route(
        web_session_key(user_id), encode_forward_event(user_id, frame)
    )
    return 0


def _get_settings(websocket: WebSocket) -> WebSettings:
    return websocket.app.state.web_deps.web_settings


def _get_task_manager(websocket: WebSocket) -> TaskManager:
    return websocket.app.state.web_deps.task_manager


@ws_router.websocket("/api/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    settings: WebSettings = _get_settings(websocket)
    await websocket.accept()
    state = ConnectionState(ws=websocket, ws_session_id=secrets.token_hex(8))

    # Step 3: Register message handlers from the chat layer via the
    # handler registry on ConnectionState.  This avoids a direct import
    # of chat.chat_auth from websocket.py, breaking the circular dep.
    _register_msg_handlers(state, settings)

    await websocket.send_json(
        {
            "type": SERVER_HELLO,
            "data": {"heartbeat_interval": settings.heartbeat_interval * 1000},
        }
    )

    try:
        raw = await asyncio.wait_for(websocket.receive_json(), timeout=30)
    except TimeoutError:
        logger.warning("ws close 4001: identify timeout (client did not send identify within 30s)")
        await websocket.close(4001, "Identify timeout")
        return
    except WebSocketDisconnect:
        # 客户端已主动断开（1001 等），连接已关，不能再发 close——
        # 否则 starlette 抛 RuntimeError: Unexpected ASGI message 'websocket.close'。
        return

    parsed = parse_client_message(raw)
    if not isinstance(parsed, IdentifyMessage):
        logger.warning("ws close 4002: first message not identify, got type=%s", raw.get("type") if isinstance(raw, dict) else type(raw).__name__)
        await websocket.close(4002, "First message must be identify")
        return

    user_id = verify_jwt(parsed.token, settings.jwt_secret)
    if user_id is None:
        logger.warning("ws auth failed: token=%s... (first 20 chars)", parsed.token[:20] if parsed.token else "EMPTY")
        await websocket.close(4003, "Invalid token")
        return

    # Protocol version check
    if parsed.protocol_version != PROTOCOL_VERSION:
        logger.warning(
            "ws version mismatch: client=%d server=%d",
            parsed.protocol_version, PROTOCOL_VERSION,
        )
        await websocket.close(4005, ProtocolErrorCode.VERSION_MISMATCH.value)
        return

    conn_registry = _get_connection_registry(websocket)
    if conn_registry.count(user_id) >= settings.max_connections_per_user:
        await websocket.close(4004, "Max connections exceeded")
        return

    state.user_id = user_id
    state.authenticated = True
    conn_registry.connect(user_id, websocket)

    from prismclaw.app.web.deps import WebDeps

    web_deps: WebDeps = websocket.app.state.web_deps

    await send_event(
        state,
        SERVER_READY,
        "",
        {
            "user_id": user_id,
            "ws_session_id": state.ws_session_id,
            "conversations": [],
            "runtime_mode": web_deps.runtime_mode_snapshot,
            "protocol_version": PROTOCOL_VERSION,
        },
    )

    gateway_router = web_deps.gateway_router
    if gateway_router is not None:
        try:
            await gateway_router.affinity.force_claim(f"web:{user_id}")
            logger.info("web affinity claimed: web:%s -> %s", user_id, gateway_router.worker_id)
        except Exception:
            logger.warning(
                "failed to register web affinity for user=%s; routing will fall back to local",
                user_id,
                exc_info=True,
            )
    else:
        logger.debug("web WS connected; affinity gateway disabled (single-worker mode)")

    task_manager = _get_task_manager(websocket)
    heartbeat_task_id = task_manager.spawn(
        f"ws-heartbeat:{state.ws_session_id}",
        _heartbeat_loop(state, settings),
        category="heartbeat",
        owner=f"web:{user_id}",
    )

    try:
        await _dispatch_loop(state, settings, task_manager)
    except WebSocketDisconnect:
        pass
    except RuntimeError:
        # heartbeat 超时/发送失败路径主动 close 后，挂起的 receive 可能以
        # RuntimeError（receive after close）唤醒——属正常关闭，静默退出。
        pass
    finally:
        await task_manager.cancel(heartbeat_task_id)
        conn_registry.disconnect(user_id, websocket)


async def _heartbeat_loop(state: ConnectionState, settings: WebSettings) -> None:
    try:
        while True:
            await asyncio.sleep(settings.heartbeat_interval)
            try:
                await state.ws.send_json({"type": SERVER_PING})
            except Exception:
                # 发送失败说明 server→client 方向已死。必须主动 close 让
                # dispatch loop 的 receive 收到断开并退出（进而触发 finally
                # 里的 registry.disconnect），否则死连接会在 registry 中
                # 悬挂到 TCP 自行拆除为止，期间持续占用
                # max_connections_per_user 配额（表现为后续连接 4004）。
                try:
                    await state.ws.close(4006, "Heartbeat send failed")
                except Exception:
                    pass
                return
            if time.time() - state.last_pong > settings.pong_timeout + settings.heartbeat_interval:
                logger.warning("pong timeout for user %s, closing", state.user_id)
                try:
                    await state.ws.close(4006, "Pong timeout")
                except Exception:
                    pass
                return
    except asyncio.CancelledError:
        return


def _register_msg_handlers(state: ConnectionState, settings: WebSettings) -> None:
    """Populate ``state.msg_handlers`` with chat-layer callbacks.

    The import of :mod:`prismclaw.channels.web.chat.chat_auth` is deferred
    to this call-site so that websocket.py never has a top-level (or
    in-dispatch-loop) import of the chat layer, breaking the circular dep.
    """
    from prismclaw.app.web.chat.chat_auth import enqueue_chat, handle_abort
    from prismclaw.app.web.chat.chat_protocol import _get_session_queue

    async def _on_chat_send(
        state: ConnectionState,
        msg: ChatSendMessage,
        task_manager: TaskManager,
    ) -> None:
        conversation_id = msg.conversation_id

        # 命令拦截: /command_name args → 插件命令 runtime
        if msg.content and msg.content.startswith("/"):
            try:
                from prismclaw.app.web.chat.command_adapter import (
                    COMMAND_HANDLED,
                    try_handle_slash_command,
                )
                modified_msg = await try_handle_slash_command(state, msg)
                if modified_msg is COMMAND_HANDLED:
                    # Built-in command fully handled locally (reply already
                    # sent over the WS) — swallow, do not enqueue to LLM.
                    return
                if modified_msg is not None:
                    msg = modified_msg
            except Exception:
                logger.warning("command_adapter interception failed", exc_info=True)

        async def _notify_enqueue_error(exc: Exception) -> None:
            try:
                await send_event(
                    state,
                    SERVER_ERROR,
                    conversation_id,
                    {"code": ProtocolErrorCode.INTERNAL_ERROR.value, "message": f"{type(exc).__name__}: {exc}"},
                )
            except Exception:
                logger.warning("ws-enqueue error notification failed", exc_info=True)

        def _on_enqueue_error(exc: Exception) -> None:
            logger.warning("ws-enqueue failed: %s", exc)
            try:
                task_manager.spawn(
                    f"ws-enqueue-error:{state.ws_session_id}",
                    _notify_enqueue_error(exc),
                    category="generic",
                    owner=f"web:{state.user_id}",
                )
            except Exception:
                logger.warning("ws-enqueue error spawn failed", exc_info=True)

        task_manager.spawn(
            f"ws-enqueue:{state.ws_session_id}",
            enqueue_chat(state, msg, settings),
            category="generic",
            on_error=_on_enqueue_error,
            owner=f"web:{state.user_id}",
        )

    async def _on_chat_abort(
        state: ConnectionState,
        msg: ChatAbortMessage,
    ) -> None:
        await handle_abort(state, msg)

    async def _on_tool_clarify(
        state: ConnectionState,
        msg: ToolClarifyMessage,
    ) -> None:
        # AskUserQuestion 澄清回传：直接解除引擎侧 wait_for_clarification 的
        # await（按 approval_id 提交答案），引擎同轮续跑。无需入队新消息。
        from prismclaw.app.web.chat.chat_protocol import _get_session_queue
        session_queue = _get_session_queue(state)
        session_queue.set_approval_decision(
            conversation_id=msg.conversation_id,
            approval_id=msg.approval_id,
            tool_call_id=msg.tool_call_id,
            approved=True,
            answer=msg.answer,
        )

    async def _on_chat_steer(
        state: ConnectionState,
        msg: ChatSteerMessage,
    ) -> None:
        # 排队消息提升：按 client_message_id 标记，consumer 取出时转入
        # steering 队列，由引擎在 iteration 边界消费注入当前 run。
        session_queue = _get_session_queue(state)
        outcome, queued_behind = session_queue.mark_steered(
            msg.conversation_id,
            msg.client_message_id,
        )
        await send_event(
            state,
            SERVER_CHAT_STEERING_ACK,
            msg.conversation_id,
            {
                "action": "steer",
                "client_message_id": msg.client_message_id,
                "outcome": outcome,
                "queued_behind": queued_behind,
            },
        )

    async def _on_chat_cancel(
        state: ConnectionState,
        msg: ChatCancelMessage,
    ) -> None:
        # 排队消息取消：consumer 取出时跳过执行（不注入、不启动 run）。
        session_queue = _get_session_queue(state)
        outcome = session_queue.mark_cancelled(
            msg.conversation_id,
            msg.client_message_id,
        )
        await send_event(
            state,
            SERVER_CHAT_STEERING_ACK,
            msg.conversation_id,
            {
                "action": "cancel",
                "client_message_id": msg.client_message_id,
                "outcome": outcome,
            },
        )

    state.msg_handlers["chat.send"] = _on_chat_send
    state.msg_handlers["chat.abort"] = _on_chat_abort
    state.msg_handlers["chat.steer"] = _on_chat_steer
    state.msg_handlers["chat.cancel"] = _on_chat_cancel
    state.msg_handlers["tool.clarify"] = _on_tool_clarify

    # Register session queue resolver for chat session handling
    # without importing chat.chat_protocol directly.
    state.get_session_queue = _get_session_queue


async def _dispatch_loop(
    state: ConnectionState,
    settings: WebSettings,
    task_manager: TaskManager,
) -> None:
    while True:
        raw = await state.ws.receive_json()
        msg = parse_client_message(raw)

        if isinstance(msg, PongMessage):
            state.last_pong = time.time()

        elif isinstance(msg, ChatSendMessage):
            handler = state.msg_handlers.get("chat.send")
            if handler is not None:
                await handler(state, msg, task_manager)

        elif isinstance(msg, ChatAbortMessage):
            handler = state.msg_handlers.get("chat.abort")
            if handler is not None:
                await handler(state, msg)

        elif isinstance(msg, ChatSteerMessage):
            handler = state.msg_handlers.get("chat.steer")
            if handler is not None:
                await handler(state, msg)

        elif isinstance(msg, ChatCancelMessage):
            handler = state.msg_handlers.get("chat.cancel")
            if handler is not None:
                await handler(state, msg)

        elif isinstance(msg, ToolClarifyMessage):
            handler = state.msg_handlers.get("tool.clarify")
            if handler is not None:
                await handler(state, msg)

        else:
            await send_event(
                state,
                SERVER_ERROR,
                "",
                {
                    "code": ProtocolErrorCode.INVALID_REQUEST.value,
                    "message": f"Unknown message type: {raw.get('type', '?')}",
                },
            )
