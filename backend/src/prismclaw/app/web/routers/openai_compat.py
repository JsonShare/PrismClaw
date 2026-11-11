from __future__ import annotations

import json
import secrets
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from prismclaw.app.auth import resolve_profile_and_mode
from prismclaw.app.session_router import SessionRouter
from prismclaw.app.auth.auth import get_current_user
from prismclaw.schema.agent.types import RunRequest
from prismclaw.schema.services.chat_service import ChatService
from prismclaw.infra.settings import WebSettings
from prismclaw.schema.content.streaming import Done, ReasoningChunk, TextChunk

openai_router = APIRouter(prefix="/api/v1", tags=["openai"])

_chat_service: ChatService | None = None
_session_router: SessionRouter | None = None
_workspace_base: Path | None = None
_web_settings: WebSettings | None = None
_redis_client: Any = None
_sandbox_policy: Any = None


def _require_sandbox_policy(sandbox_policy: Any) -> Any:
    if sandbox_policy is None:
        raise RuntimeError(
            "openai web runtime requires sandbox_policy to be resolved before dependency wiring"
        )
    return sandbox_policy


def set_openai_deps(
    chat_service: ChatService,
    session_router: SessionRouter,
    sandbox_policy: Any,
    workspace_base: Path | None = None,
    web_settings: WebSettings | None = None,
    redis_client: Any = None,
) -> None:
    global _chat_service, _session_router, _workspace_base, _web_settings, _redis_client, _sandbox_policy
    _chat_service = chat_service
    _session_router = session_router
    _workspace_base = workspace_base
    _web_settings = web_settings
    _redis_client = redis_client
    _sandbox_policy = _require_sandbox_policy(sandbox_policy)


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str = "default"
    messages: list[ChatMessage]
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None
    user: str | None = None


@openai_router.post("/chat/completions")
async def chat_completions(
    body: ChatCompletionRequest,
    user_id: str = Depends(get_current_user),
) -> Any:
    if _chat_service is None or _session_router is None:
        raise HTTPException(500, "OpenAI compat deps not configured")

    # Task 10.3: ignore body.user to prevent session spoofing
    session_key = f"openai:{user_id}"

    user_messages = [m for m in body.messages if m.role == "user"]
    if not user_messages:
        raise HTTPException(400, "No user message")
    last_msg = user_messages[-1].content

    session_id, _tree = await _session_router.resolve_or_create(session_key, "default")

    user_profile = None
    mode_override_value = None
    if _web_settings is not None:
        user_profile, mode_override_value = await resolve_profile_and_mode(
            channel="web",
            user_id=user_id,
            redis_client=_redis_client,
            message_mode=None,
            deployment_default=_web_settings.default_mode,
        )

    request = RunRequest(
        session_id=session_id,
        workspace_id="default",
        agent_id="default",
        user_message=last_msg,
        mode_override=mode_override_value,
        user_id=user_id,
        user_profile=user_profile,
        sandbox_policy=_sandbox_policy,
    )

    if body.stream:
        return StreamingResponse(
            _stream_sse(request, body.model, user_id),
            media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no"},
        )

    return await _complete_response(request, body.model, user_id)


def _resolve_user_workspace(user_id: str) -> Path:
    assert _workspace_base is not None, "workspace_base not configured"
    user_workspace = _workspace_base / f"web_{user_id}"
    user_workspace.mkdir(parents=True, exist_ok=True)
    from prismclaw.app.web._routes_helpers import ensure_managed_workspace_dirs

    # OpenAI 兼容路由没有 workspace_id 维度：web_<uid>/ 本身就是本次运行的
    # workspace 根，与 /chat 的 <ws> 使用同一套目录脚手架。
    ensure_managed_workspace_dirs(user_workspace)
    return user_workspace


async def _stream_sse(request: RunRequest, model: str, user_id: str):
    assert _chat_service is not None
    completion_id = f"chatcmpl-{secrets.token_hex(12)}"

    # Task 10.4: per-user workspace isolation
    tool_workspace = _resolve_user_workspace(user_id)

    first_chunk = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
    }
    yield f"data: {json.dumps(first_chunk)}\n\n"

    async for event in _chat_service.start_conversation(request, tool_workspace_path=tool_workspace):
        if isinstance(event, TextChunk):
            chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": event.text},
                        "finish_reason": None,
                    }
                ],
            }
            yield f"data: {json.dumps(chunk)}\n\n"
        elif isinstance(event, ReasoningChunk):
            # Transparent passthrough as ``delta.reasoning_content`` — the
            # OpenAI-compat field used by GLM/Qwen/DeepSeek reasoning models.
            # Keeps the SSE channel consistent with the WS channel so reasoning
            # is not silently dropped here.
            chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"reasoning_content": event.reasoning},
                        "finish_reason": None,
                    }
                ],
            }
            yield f"data: {json.dumps(chunk)}\n\n"
        elif isinstance(event, Done):
            final: dict[str, Any] = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
            if event.usage:
                final["usage"] = _format_openai_usage(event.usage)
            yield f"data: {json.dumps(final)}\n\n"
            break

    yield "data: [DONE]\n\n"


async def _complete_response(request: RunRequest, model: str, user_id: str) -> dict[str, Any]:
    assert _chat_service is not None
    completion_id = f"chatcmpl-{secrets.token_hex(12)}"
    collected_text: list[str] = []
    collected_reasoning: list[str] = []
    usage: dict[str, int] = {}

    tool_workspace = _resolve_user_workspace(user_id)

    async for event in _chat_service.start_conversation(request, tool_workspace_path=tool_workspace):
        if isinstance(event, TextChunk):
            collected_text.append(event.text)
        elif isinstance(event, ReasoningChunk):
            collected_reasoning.append(event.reasoning)
        elif isinstance(event, Done):
            usage = event.usage
            break

    message: dict[str, Any] = {
        "role": "assistant",
        "content": "".join(collected_text),
    }
    # Reasoning passthrough (non-stream path). Only attached when present so
    # non-reasoning models get the standard shape unchanged.
    reasoning_text = "".join(collected_reasoning)
    if reasoning_text:
        message["reasoning_content"] = reasoning_text

    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "stop",
            }
        ],
        "usage": _format_openai_usage(usage),
    }


def _format_openai_usage(done_usage: dict[str, int]) -> dict[str, Any]:
    input_tokens = done_usage.get("input", 0)
    output_tokens = done_usage.get("output", 0)
    cache_read = done_usage.get("cache_read", 0)
    return {
        "prompt_tokens": input_tokens,
        "completion_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "prompt_tokens_details": {"cached_tokens": cache_read},
    }


@openai_router.get("/models")
async def list_models() -> dict[str, Any]:
    return {
        "object": "list",
        "data": [{"id": "prismclaw-default", "object": "model", "owned_by": "prismclaw"}],
    }
