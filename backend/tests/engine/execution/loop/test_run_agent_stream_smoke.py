"""端到端 smoke：RunRequest → run_agent_stream → teardown。

覆盖 prepare / loop / teardown 全链路，锁定阶段 2 容器收敛的
「零执行覆盖」风险：纯文本回合应正常 Done、terminated_by=DONE，
且 SESSION_END hook 恰好触发一次。

仅替换外部边界（LLM / hook 分发），其余走真实装配。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from tests.engine.session_store import FakeRedisSessionStore

from prismclaw.engine.execution.capabilities.catalog import CapabilityCatalogService
from prismclaw.engine.execution.loop.deps import AgentRunnerDeps
from prismclaw.engine.execution.loop.runner import run_agent_stream
from prismclaw.engine.hooks import HookEvent
from prismclaw.engine.plugins.hooks.schema import HookDecision, HookOutcome, HookResult
from prismclaw.engine.toolkit.registry.registry import ToolRegistry
from prismclaw.schema.agent.types import RunRequest
from prismclaw.schema.content.streaming import Done
from prismclaw.schema.llm.types import LLMResponse, LLMStreamChunk, LLMUsage

_TEXT = "smoke-ok"


class _StubLLM:
    """最小 LLM：一次文本流，无工具调用。"""

    default_model = "stub-model"

    async def stream(
        self,
        *,
        request,
        model=None,
        tools=None,
        prompt_cache_runtime=None,
        idle_seconds=0.0,
        abort_event=None,
        temperature=None,
    ):
        yield LLMStreamChunk(
            text_delta=_TEXT,
            finish_reason="stop",
            usage=LLMUsage(input_tokens=5, output_tokens=3, total_tokens=8),
        )

    async def complete(self, **kwargs):
        return LLMResponse(
            text=_TEXT,
            tool_calls=[],
            usage=LLMUsage(input_tokens=5, output_tokens=3, total_tokens=8),
            finish_reason="stop",
        )


class _RecordingHookDispatcher:
    """记录 hook 事件；返回中性决策。"""

    def __init__(self) -> None:
        self.events: list[tuple[HookEvent, dict]] = []

    async def dispatch(self, event, payload):
        self.events.append((event, dict(payload or {})))
        return HookResult(outcome=HookOutcome.SUCCESS)

    def session_end_payloads(self) -> list[dict]:
        return [payload for event, payload in self.events if event == HookEvent.SESSION_END]


@pytest.mark.asyncio
async def test_run_agent_stream_smoke_prepare_loop_teardown(tmp_path):
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
async def test_loop_state_receives_assembled_runtime(tmp_path, monkeypatch):
    """RunLoopState 必须拿到 build_runtime 原地装配后的字段。

    回归守卫：若先构造 RunLoopState、再 build_loop_config(rt)，
    tool_loop_state 会是 None（纯文本回合不会因此崩溃，故只能显式断言）。
    """
    import prismclaw.engine.execution.loop.runner as runner_module

    dispatcher = _RecordingHookDispatcher()
    deps = _make_deps(dispatcher)
    captured: dict[str, object] = {}
    real_run_loop = runner_module.run_loop

    async def _capture(state, config):
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
async def test_hook_block_takes_early_exit(tmp_path):
    """USER_PROMPT_SUBMIT 被阻断时必须走 EarlyExit 并在 teardown 上报 early_exit。"""

    class _BlockingDispatcher(_RecordingHookDispatcher):
        async def dispatch(self, event, payload):
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


def _make_deps(dispatcher: _RecordingHookDispatcher) -> AgentRunnerDeps:
    return AgentRunnerDeps(
        llm=_StubLLM(),
        tools=ToolRegistry(),
        session_store=FakeRedisSessionStore(),
        capability_catalog=CapabilityCatalogService(),
        hook_dispatcher=dispatcher,
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
