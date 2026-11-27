"""端到端 smoke：RunRequest → run_agent_stream → teardown。

覆盖 prepare / loop / teardown 全链路，锁定阶段 2 容器收敛的
「零执行覆盖」风险：纯文本回合应正常 Done、terminated_by=DONE，
且 SESSION_END hook 恰好触发一次。

仅替换外部边界（LLM / hook 分发），其余走真实装配。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from tests.engine.session_store import FakeRedisSessionStore

from prismclaw.engine.execution.capabilities.catalog import RuntimeCatalog
from prismclaw.engine.execution.loop.deps import AgentRunnerDeps, ExpertBundle
from prismclaw.engine.experts.registry.expert_registry import ExpertRegistry
from prismclaw.infra.config.capability_policy import CapabilityPolicyRegistry
from prismclaw.infra.environment_catalog import EnvironmentCatalog
from prismclaw.engine.execution.loop.runner import run_agent_stream
from prismclaw.engine.execution.loop.runtime import LoopRuntime
from prismclaw.engine.execution.loop.types import LoopConfig, RunLoopState
from prismclaw.engine.hooks import HookDispatcher, HookEvent
from prismclaw.engine.hooks.schema import HookDecision, HookOutcome, HookResult
from prismclaw.engine.toolkit.registry.registry import ToolRegistry
from prismclaw.schema.agent.capability import JsonValue
from prismclaw.schema.agent.types import RunRequest
from prismclaw.schema.content.streaming import AgentEvent, Done, ErrorEvent
from prismclaw.schema.llm.request import PackedLLMRequest
from prismclaw.schema.llm.types import LLMResponse, LLMStreamChunk, LLMUsage
from prismclaw.schema.session.types import SessionHeader, SessionTree

_TEXT = "smoke-ok"


class _StubLLM:
    """最小 LLM：一次文本流，无工具调用。"""

    default_model = "stub-model"

    async def stream(
        self,
        *,
        request: PackedLLMRequest,
        model: str | None = None,
        tools: list[dict[str, JsonValue]] | None = None,
        prompt_cache_runtime: object | None = None,
        idle_seconds: float = 0.0,
        abort_event: asyncio.Event | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[LLMStreamChunk]:
        yield LLMStreamChunk(
            text_delta=_TEXT,
            finish_reason="stop",
            usage=LLMUsage(input_tokens=5, output_tokens=3, total_tokens=8),
        )

    async def complete(self, **kwargs: object) -> LLMResponse:
        return LLMResponse(
            text=_TEXT,
            tool_calls=[],
            usage=LLMUsage(input_tokens=5, output_tokens=3, total_tokens=8),
            finish_reason="stop",
        )


class _RecordingHookDispatcher:
    """记录 hook 事件；返回中性决策。"""

    def __init__(self) -> None:
        self.events: list[tuple[HookEvent, dict[str, object]]] = []

    async def dispatch(
        self, event: HookEvent, payload: dict[str, object] | None
    ) -> HookResult:
        self.events.append((event, dict(payload or {})))
        return HookResult(outcome=HookOutcome.SUCCESS)

    def session_end_payloads(self) -> list[dict[str, object]]:
        return [payload for event, payload in self.events if event == HookEvent.SESSION_END]


@pytest.mark.asyncio
async def test_run_agent_stream_smoke_prepare_loop_teardown(tmp_path: Path) -> None:
    dispatcher = _RecordingHookDispatcher()
    deps = _make_deps(dispatcher)
    request = _make_request()

    events = []
    async for event in run_agent_stream(request, deps, tool_workspace_path=tmp_path):
        events.append(event)

    assert any(isinstance(event, Done) for event in events), (
        f"expected Done event, got {[type(e).__name__ for e in events]}"
    )

    payloads = dispatcher.session_end_payloads()
    assert len(payloads) == 1, f"expected exactly one SESSION_END, got {payloads}"
    assert payloads[0]["terminated_by"] == "done"
    assert payloads[0]["session_id"] == "smoke-session"


@pytest.mark.asyncio
async def test_loop_state_receives_assembled_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RunLoopState 必须拿到 build_runtime 原地装配后的字段。

    回归守卫：若先构造 RunLoopState、再 build_loop_config(rt)，
    tool_loop_state 会是 None（纯文本回合不会因此崩溃，故只能显式断言）。
    """
    import prismclaw.engine.execution.loop.runner as runner_module
    from prismclaw.engine.execution.loop.agent_loop import run_loop as real_run_loop

    dispatcher = _RecordingHookDispatcher()
    deps = _make_deps(dispatcher)
    captured: dict[str, RunLoopState] = {}

    async def _capture(state: RunLoopState, config: LoopConfig) -> AsyncIterator[AgentEvent]:
        captured["state"] = state
        async for event in real_run_loop(state, config):
            yield event

    monkeypatch.setattr(runner_module, "run_loop", _capture)

    async for _ in run_agent_stream(_make_request(), deps, tool_workspace_path=tmp_path):
        pass

    state = captured["state"]
    assert state.tool_loop_state is not None
    assert state.state_machine is not None


@pytest.mark.asyncio
async def test_preparer_injects_session_expert_into_tool_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import prismclaw.engine.execution.loop.runner as runner_module

    store = FakeRedisSessionStore()
    await store.save_header(
        SessionTree(
            header=SessionHeader(
                id="smoke-session",
                workspace_id="smoke-workspace",
                agent_id="smoke-agent",
                expert_id="medical-clinical",
            )
        )
    )
    from prismclaw.engine.execution.loop.wiring import (
        build_loop_config as real_build_loop_config,
    )

    deps = _make_deps(_RecordingHookDispatcher(), session_store=store)
    captured: dict[str, str | None] = {}

    def _capture(runtime: LoopRuntime) -> LoopConfig:
        captured["expert_id"] = runtime.tool_ctx.expert_id
        return real_build_loop_config(runtime)

    monkeypatch.setattr(runner_module, "build_loop_config", _capture)

    async for _ in run_agent_stream(_make_request(), deps, tool_workspace_path=tmp_path):
        pass

    assert captured["expert_id"] == "medical-clinical"


class _EmptyCapabilityCatalog:
    def known_ids(self, kind: str) -> frozenset[str]:
        return frozenset()


def _expert_bundle(tmp_path: Path) -> ExpertBundle:
    state_path = tmp_path / "capability_policy.yaml"
    state_path.write_text("version: 1\n", encoding="utf-8")
    return ExpertBundle(
        environment_catalog=EnvironmentCatalog(
            expert_registry=ExpertRegistry([]),
            capability_policy_registry=CapabilityPolicyRegistry(
                state_path, catalog=_EmptyCapabilityCatalog()
            ),
        )
    )


@pytest.mark.asyncio
async def test_unavailable_expert_early_exits_before_message_append(tmp_path: Path) -> None:
    """选了不存在的专家：必须在用户消息落库前早退，且错误码可达前端。"""
    store = FakeRedisSessionStore()
    await store.save_header(
        SessionTree(
            header=SessionHeader(
                id="smoke-session",
                workspace_id="smoke-workspace",
                agent_id="smoke-agent",
                expert_id="ghost@experts",
                expert_activation_source="user",
            )
        )
    )
    dispatcher = _RecordingHookDispatcher()
    deps = _make_deps(dispatcher, session_store=store)
    deps.expert = _expert_bundle(tmp_path)

    events = []
    async for event in run_agent_stream(_make_request(), deps, tool_workspace_path=tmp_path):
        events.append(event)

    errors = [event for event in events if isinstance(event, ErrorEvent)]
    assert len(errors) == 1
    assert errors[0].error_code == "EXPERT_UNAVAILABLE"

    tree = await store.load("smoke-session")
    assert tree is not None
    assert [entry for entry in tree.get_branch() if entry.role == "user"] == []
    payloads = dispatcher.session_end_payloads()
    assert len(payloads) == 1
    assert payloads[0]["terminated_by"] == "early_exit"


@pytest.mark.asyncio
async def test_hook_block_takes_early_exit(tmp_path: Path) -> None:
    """USER_PROMPT_SUBMIT 被阻断时必须走 EarlyExit 并在 teardown 上报 early_exit。"""

    class _BlockingDispatcher(_RecordingHookDispatcher):
        async def dispatch(
            self, event: HookEvent, payload: dict[str, object] | None
        ) -> HookResult:
            result = await super().dispatch(event, payload)
            if event == HookEvent.USER_PROMPT_SUBMIT:
                return HookResult(
                    outcome=HookOutcome.SUCCESS,
                    decision=HookDecision.BLOCK,
                    decision_reason="blocked by policy",
                )
            return result

    dispatcher = _BlockingDispatcher()
    deps = _make_deps(dispatcher)

    events = []
    async for event in run_agent_stream(_make_request(), deps, tool_workspace_path=tmp_path):
        events.append(event)

    assert any(isinstance(event, Done) for event in events)
    payloads = dispatcher.session_end_payloads()
    assert len(payloads) == 1
    assert payloads[0]["terminated_by"] == "early_exit"


def _make_deps(
    dispatcher: _RecordingHookDispatcher,
    *,
    session_store: FakeRedisSessionStore | None = None,
) -> AgentRunnerDeps:
    return AgentRunnerDeps(
        llm=_StubLLM(),
        tools=ToolRegistry(),
        session_store=session_store or FakeRedisSessionStore(),
        capability_catalog=RuntimeCatalog(),
        hook_dispatcher=cast(HookDispatcher, dispatcher),
    )


def _make_request() -> RunRequest:
    return RunRequest(
        session_id="smoke-session",
        workspace_id="smoke-workspace",
        agent_id="smoke-agent",
        user_message="hello",
        request_id="smoke-request",
        sandbox_policy=SimpleNamespace(tier="standard"),
    )
