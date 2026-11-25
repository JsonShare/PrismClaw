from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Coroutine
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from prismclaw.app.web.chat import chat_runner
from prismclaw.app.web.chat.chat_protocol import SessionQueue
from prismclaw.app.web.protocol.messages import SERVER_CHAT_DONE, ChatSendMessage
from prismclaw.app.web.routers import openai_compat
from prismclaw.schema.agent.types import RunRequest
from prismclaw.schema.content.streaming import Done, TextChunk
from prismclaw.schema.shared.reference_block import (
    ReferenceSource,
    normalize_reference_sources,
)


def test_reference_sources_reject_non_http_urls_but_keep_titles() -> None:
    sources = normalize_reference_sources(
        [
            "javascript:alert(1)",
            "https://example.com/guide",
            {"title": "仅展示标题", "url": "javascript:alert(1)"},
            {"title": "对象标题", "url": "data:text/html,hello"},
            {"url": "ftp://example.com/file"},
            ReferenceSource(title="模型对象标题", url="file:///tmp/private"),
        ]
    )

    assert [(source.title, source.url) for source in sources] == [
        ("https://example.com/guide", "https://example.com/guide"),
        ("仅展示标题", None),
        ("对象标题", None),
        ("模型对象标题", None),
    ]


class _FakeChatService:
    def __init__(self, events: list[object]) -> None:
        self._events = events

    async def start_conversation(
        self,
        request: RunRequest,
        *,
        tool_workspace_path: Path,
        control: object | None = None,
    ) -> AsyncIterator[object]:
        for event in self._events:
            yield event


def _request() -> RunRequest:
    return RunRequest(
        session_id="s1",
        workspace_id="default",
        agent_id="default",
        user_message="hi",
        sandbox_policy=None,
    )


def _patch_chat_service(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    events: list[object],
) -> None:
    monkeypatch.setattr(openai_compat, "_chat_service", _FakeChatService(events))
    monkeypatch.setattr(openai_compat, "_resolve_user_workspace", lambda _user_id: tmp_path)


async def test_openai_stream_hides_split_reference_block(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_chat_service(
        monkeypatch,
        tmp_path,
        [
            TextChunk(text="答案\n<!--ref"),
            TextChunk(
                text='erences\n[{"title":"指南","url":"https://example.com"}]\n-->'
            ),
            Done(final_message="答案"),
        ],
    )

    frames = [frame async for frame in openai_compat._stream_sse(_request(), "m", "u1")]
    payloads = [
        json.loads(frame.removeprefix("data: ").strip())
        for frame in frames
        if frame.strip() != "data: [DONE]"
    ]
    content = "".join(
        payload["choices"][0]["delta"].get("content", "")
        for payload in payloads
    )

    assert content == "答案\n"
    assert "references" not in content
    assert "example.com" not in content


async def test_openai_non_stream_hides_reference_block(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_chat_service(
        monkeypatch,
        tmp_path,
        [
            TextChunk(
                text='答案\n<!--references\n[{"title":"指南","url":"https://example.com"}]\n-->'
            ),
            Done(final_message="答案"),
        ],
    )

    response = await openai_compat._complete_response(_request(), "m", "u1")
    content = response["choices"][0]["message"]["content"]

    assert content == "答案"
    assert "references" not in content
    assert "example.com" not in content


class _FakeRunnerDeps:
    def __init__(self) -> None:
        self.session_store = object()

    def with_request_context(self, **_kwargs: object) -> _FakeRunnerDeps:
        return self


async def test_url_fallback_is_persisted_before_done_and_reused_for_push(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    conversation_id = "web:u1:default:s:abc"
    session_queue = SessionQueue()
    deps = _FakeRunnerDeps()
    chat_service = _FakeChatService(
        [
            TextChunk(text="普通回答，没有结构化来源块"),
            Done(final_message="普通回答", assistant_message_id="a1"),
        ]
    )
    web_deps = SimpleNamespace(
        redis_client=None,
        sandbox_policy=object(),
        scheduler_context=None,
        run_presence=None,
        chat_service=chat_service,
    )
    state = SimpleNamespace(
        user_id="u1",
        ws=SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(web_deps=web_deps))
        ),
    )
    msg = ChatSendMessage(
        conversation_id=conversation_id,
        workspace_id="default",
        content="hi",
    )
    fallback_sources = [ReferenceSource(title="兜底来源", url="https://example.com")]
    calls: list[tuple[str, Any]] = []
    background_tasks: list[asyncio.Task[Any]] = []

    async def fake_resolve_profile(**_kwargs: object) -> None:
        return None

    async def fake_load_tree(*_args: object, **_kwargs: object) -> None:
        return None

    async def fake_workspace_deps(*_args: object, **_kwargs: object) -> _FakeRunnerDeps:
        return deps

    async def fake_noop(*_args: object, **_kwargs: object) -> None:
        return None

    async def fake_questions(*_args: object, **_kwargs: object) -> list[str]:
        return []

    async def fake_fallback(*_args: object, **_kwargs: object) -> list[ReferenceSource]:
        calls.append(("fallback", None))
        return fallback_sources

    async def fake_persist(_store: object, **kwargs: object) -> None:
        calls.append(("persist", kwargs))

    async def fake_push(_state: object, **kwargs: object) -> None:
        calls.append(("push", kwargs))

    async def fake_send_event(
        _state: object,
        event_type: str,
        _conversation_id: str,
        data: dict[str, Any],
    ) -> tuple[int, bool]:
        if event_type == SERVER_CHAT_DONE:
            calls.append(("done", data))
        return 1, True

    def fake_spawn_background(
        coro: Coroutine[Any, Any, Any],
        *,
        label: str,
        session_queue: SessionQueue,
    ) -> None:
        background_tasks.append(asyncio.create_task(coro))

    monkeypatch.setattr(chat_runner, "_get_session_queue", lambda _state: session_queue)
    monkeypatch.setattr(chat_runner, "resolve_profile", fake_resolve_profile)
    monkeypatch.setattr(chat_runner, "_get_runner_deps", lambda _state: deps)
    monkeypatch.setattr(chat_runner, "_load_tree_for_write", fake_load_tree)
    monkeypatch.setattr(chat_runner, "_get_workspace_runner_deps", fake_workspace_deps)
    monkeypatch.setattr(chat_runner, "_resolve_tool_workspace_path", lambda **_kwargs: tmp_path)
    monkeypatch.setattr(chat_runner, "_push_task_runtime_event", fake_noop)
    monkeypatch.setattr(chat_runner, "_persist_execution_timeline", fake_noop)
    monkeypatch.setattr(chat_runner, "_generate_followup_questions_for_request", fake_questions)
    monkeypatch.setattr(chat_runner, "_generate_reference_sources", fake_fallback)
    monkeypatch.setattr(chat_runner, "_persist_reference_sources_metadata", fake_persist)
    monkeypatch.setattr(chat_runner, "_push_reference_event", fake_push)
    monkeypatch.setattr(chat_runner, "send_event", fake_send_event)
    monkeypatch.setattr(chat_runner, "_spawn_background", fake_spawn_background)

    await chat_runner._run_chat(state, msg)
    if background_tasks:
        await asyncio.gather(*background_tasks)

    assert [name for name, _payload in calls] == ["fallback", "persist", "done", "push"]
    persist_kwargs = calls[1][1]
    push_kwargs = calls[3][1]
    assert persist_kwargs["message_id"] == "a1"
    assert persist_kwargs["sources"] == [source.model_dump() for source in fallback_sources]
    assert push_kwargs["assistant_message_id"] == "a1"
    assert push_kwargs["sources"] == fallback_sources
