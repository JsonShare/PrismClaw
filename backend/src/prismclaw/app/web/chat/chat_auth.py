from __future__ import annotations

import asyncio
import logging

from prismclaw.app.web.chat.chat_protocol import (
    _get_session_queue,
)
from prismclaw.app.web.connection import ConnectionState, send_event
from prismclaw.app.web.protocol.messages import (
    SERVER_CHAT_DONE,
    SERVER_CHAT_QUEUED,
    SERVER_ERROR,
    ChatAbortMessage,
    ChatSendMessage,
)

logger = logging.getLogger(__name__)


async def enqueue_chat(
    state: ConnectionState,
    msg: ChatSendMessage,
) -> None:
    session_queue = _get_session_queue(state)
    if session_queue._task_manager is None:
        web_deps = state.ws.app.state.web_deps
        tm = web_deps.task_manager
        if tm is not None:
            session_queue.set_task_manager(tm)

    conversation_id = msg.conversation_id

    async def _handle(m: ChatSendMessage) -> None:
        from prismclaw.app.web.chat.chat_runner import _run_chat

        try:
            await _run_chat(state, m)
        except asyncio.CancelledError:
            raise
        except Exception:
            # _run_chat 在发出首个事件前失败时（工作区/会话目录不可写、依赖装配
            # 异常等），此前只有 consumer 记日志，前端永远停在「AI 正在处理」。
            # 这里补一个错误帧 + 终止帧，保证 UI 能退出 busy 状态。
            logger.exception(
                "chat run failed before terminal event: conversation=%s",
                m.conversation_id,
            )
            await send_event(
                state,
                SERVER_ERROR,
                m.conversation_id,
                {
                    "message": "任务启动失败，请检查工作区权限后重试。",
                    "code": "internal_error",
                },
            )
            await send_event(
                state,
                SERVER_CHAT_DONE,
                m.conversation_id,
                {"final_message": "", "usage": {}, "aborted": True},
            )

    position = await session_queue.enqueue(conversation_id, msg, _handle)
    if position > 0:
        await send_event(
            state,
            SERVER_CHAT_QUEUED,
            conversation_id,
            {
                "position": position,
            },
        )


async def handle_abort(
    state: ConnectionState,
    msg: ChatAbortMessage,
) -> None:
    logger.info(
        "handle_abort called: conversation_id=%s, user_id=%s",
        msg.conversation_id,
        state.user_id,
    )
    session_queue = _get_session_queue(state)
    rc = session_queue.get_run_control(msg.conversation_id)
    if rc.is_active():
        # 只发中止信号，不在这里发 chat.done：done 的契约是"本轮已完全落库"，
        # 此刻已流出的正文还没落库。收尾（锚点复用 / 中断补写 + timeline + 交付物
        # + 发 done）由 chat_runner 的 ErrorEvent(aborted) 路径统一完成。
        rc.stop()
        session_queue.release_pending_approvals(msg.conversation_id)
    else:
        # 非 owner worker：本地没有这条 run 的控制块（get_run_control 会惰性
        # 造一个空块，is_active 恒为 False）。改发跨 worker 中止请求，由 owner
        # 的常驻监听转成本地 rc.stop()；否则多副本下停止按钮静默无效。
        presence = getattr(getattr(state.ws.app.state, "web_deps", None), "run_presence", None)
        if presence is not None:
            await presence.request_abort(msg.conversation_id)
    logger.info(
        "handle_abort: conversation_id=%s, active=%s",
        msg.conversation_id,
        rc.is_active(),
    )
