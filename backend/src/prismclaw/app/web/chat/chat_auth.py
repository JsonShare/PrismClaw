from __future__ import annotations

import logging

from prismclaw.app.web.chat.chat_protocol import (
    _get_session_queue,
)
from prismclaw.app.web.protocol.messages import (
    SERVER_CHAT_DONE,
    SERVER_CHAT_QUEUED,
    ChatAbortMessage,
    ChatSendMessage,
)
from prismclaw.app.web.connection import ConnectionState, send_event
from prismclaw.infra.settings import WebSettings

logger = logging.getLogger(__name__)


async def enqueue_chat(
    state: ConnectionState,
    msg: ChatSendMessage,
    settings: WebSettings,
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
        await _run_chat(state, m, settings)

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
        rc.chat_done_handled_externally = True
        rc.stop()
        session_queue.release_pending_approvals(msg.conversation_id)
        await send_event(
            state,
            SERVER_CHAT_DONE,
            msg.conversation_id,
            {"final_message": "", "usage": {}, "aborted": True},
        )
    logger.info(
        "handle_abort: conversation_id=%s, active=%s",
        msg.conversation_id,
        rc.is_active(),
    )