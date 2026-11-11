from __future__ import annotations

import asyncio
import logging
from typing import Any

from prismclaw.app.web.protocol.messages import (
    SERVER_QUESTIONS,
    SERVER_REFERENCE,
)
from prismclaw.app.web.connection import ConnectionState, send_event
from prismclaw.schema.services.chat_assistant import (
    extract_reference_sources,
    generate_followup_questions,
)

logger = logging.getLogger(__name__)


async def _generate_followup_questions_for_request(
    deps: Any,
    *,
    session_id: str,
    user_message: str,
    raw_attachments: list[Any],
    assistant_text: str | None = None,
    max_turns: int = 5,
) -> list[str]:
    return await generate_followup_questions(
        deps.llm,
        deps.session_store,
        session_id=session_id,
        user_message=user_message,
        raw_attachments=raw_attachments,
        assistant_text=assistant_text,
        max_turns=max_turns,
    )


async def _await_questions_and_push(
    state: ConnectionState,
    *,
    conversation_id: str,
    assistant_message_id: str | None,
    base_questions_task: asyncio.Task[list[str]] | None,
    refined_questions_task: asyncio.Task[list[str]] | None,
    timeout_seconds: float = 12.0,
) -> None:
    loop = asyncio.get_running_loop()
    started = loop.time()
    questions: list[str] | None = None
    # 追问生成走的是带推理的聊天模型（实测 reasoning 阶段就 >1.5s），
    # 预算给太紧会让任务被 cancel 而一条问题都发不出去；这里只在回答
    # 已结束后于后台等待，不阻塞用户，故放宽到 8s。
    refined_timeout = min(8.0, timeout_seconds)

    if refined_questions_task is not None:
        try:
            questions = await asyncio.wait_for(refined_questions_task, timeout=refined_timeout)
        except asyncio.TimeoutError:
            pass
        except Exception:
            logger.debug("refined_questions failed", exc_info=True)

    if questions is None and base_questions_task is not None:
        remaining = timeout_seconds - (loop.time() - started)
        if remaining > 0:
            try:
                questions = await asyncio.wait_for(base_questions_task, timeout=remaining)
            except asyncio.TimeoutError:
                pass
            except Exception:
                logger.debug("base_questions failed", exc_info=True)

    if questions is None:
        return
    if not questions:
        return
    data: dict[str, Any] = {"questions": questions}
    if assistant_message_id:
        data["message_id"] = assistant_message_id
    try:
        await send_event(state, SERVER_QUESTIONS, conversation_id, data)
    except Exception:
        logger.warning("web.questions.send_failed", exc_info=True)


async def _generate_reference_sources(
    deps: Any,
    *,
    session_id: str,
    max_turns: int = 5,
) -> list[str]:
    return await extract_reference_sources(
        deps.session_store,
        session_id=session_id,
        max_turns=max_turns,
    )


async def _push_reference_event(
    state: ConnectionState,
    *,
    deps: Any,
    conversation_id: str,
    session_id: str,
    assistant_message_id: str | None,
) -> None:
    sources = await _generate_reference_sources(deps, session_id=session_id, max_turns=5)
    if not sources:
        return
    data: dict[str, Any] = {"sources": sources}
    if assistant_message_id:
        data["message_id"] = assistant_message_id
    try:
        await send_event(state, SERVER_REFERENCE, conversation_id, data)
    except Exception:
        logger.warning("web.reference.send_failed", exc_info=True)
