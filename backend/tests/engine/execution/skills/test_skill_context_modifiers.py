"""技能上下文修饰通道契约测试（t8 复核补盲）。

锁定 `_apply_context_modifiers_from_results` 的行为契约——本通道是
execute_tools 闭包中 `state.pending_meta_messages` 的消费/写回对象
（Phase 3 由 `meta_messages_override` 更名保留，SDD 偏差 1）。

语义（改名前后一致，此测试锁定防回归）：
- messages_override 是累积容器：返回值在其尾部追加技能切换提示与
  modifier.append_messages（调用方全量替换 state.pending_meta_messages）
- 技能实际变化（激活/切换）时记录 context injection；同技能重复
  断言不产生新 injection
- resolved invocation state 来自 modifier，并经
  project_active_skill_invocation 投影
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from prismclaw.engine.execution.skills.skill_dispatch import (
    _apply_context_modifiers_from_results,
)
from prismclaw.schema.content.content_types import ToolResult
from prismclaw.schema.services.skills import SkillInvocationState


def _make_deps() -> SimpleNamespace:
    return SimpleNamespace(
        capability_catalog=SimpleNamespace(
            get_skill_entry=lambda name: SimpleNamespace(
                instructions=f"instr-{name}",
                location="",
                plugin_name=None,
            ),
        ),
        tools=None,  # allowed_tools_override=None → 不触达 registry
    )


def _make_result(active_skill_name: str, append_text: str | None = None) -> ToolResult:
    modifier = SimpleNamespace(
        active_skill_name=active_skill_name,
        allowed_tools_override=None,
        clear_after_terminal_result=False,
        invocation_context="skill",
        append_messages=(
            [{"role": "user", "content": append_text}] if append_text else []
        ),
    )
    return ToolResult.model_construct(
        content=[],
        is_error=False,
        runtime_control=SimpleNamespace(
            skill_name=active_skill_name,
            skill_execution_strategy=None,
            context_modifier=modifier,
        ),
    )


def _run(messages_override, skill_invocation, results, monkeypatch=None):
    snapshot = SimpleNamespace(
        invoked_skill_state=SimpleNamespace(skills={}, active_invocation=None),
    )
    # project_active_skill_invocation 的落盘投影与业务无关，短路之
    with patch(
        "prismclaw.engine.compression.preservation.project_preservation_runtime",
        lambda **kwargs: None,
    ):
        return _apply_context_modifiers_from_results(
            deps=_make_deps(),
            request=SimpleNamespace(agent_id="agent-test"),
            snapshot=snapshot,
            tool_ctx=SimpleNamespace(),
            results=results,
            skill_invocation=skill_invocation,
            messages_override=messages_override,
        )


def test_skill_switch_appends_switch_and_activation_messages():
    prior = [{"role": "user", "content": "earlier-injection"}]
    invocation = SkillInvocationState.model_construct(
        skill_name="skill_a", is_active=True,
    )
    resolved, updated, injections = _run(
        prior,
        invocation,
        [_make_result("skill_b", append_text="skill-b 激活注入")],
    )

    # 累积语义：返回值 = 既有队列 + 切换提示 + 激活注入
    assert updated is not prior
    assert updated[0] == prior[0]
    assert "从技能“skill_a”切换到技能“skill_b”" in updated[1]["content"]
    assert updated[2]["content"] == "skill-b 激活注入"
    # 状态演化：解析结果跟随 modifier
    assert resolved.skill_name == "skill_b"
    # 技能变化 → 记一条 context injection
    assert len(injections) == 1
    assert injections[0].skill_name == "skill_b"
    assert injections[0].content == "skill-b 激活注入"


def test_same_skill_reassert_appends_but_no_new_injection():
    invocation = SkillInvocationState.model_construct(
        skill_name="skill_b", is_active=True,
    )
    _, updated, injections = _run(
        None,
        invocation,
        [_make_result("skill_b", append_text="重复断言")],
    )
    # 同技能重复断言：不产生切换提示、不产生新 injection
    assert len(updated) == 1
    assert updated[0]["content"] == "重复断言"
    assert injections == []


def test_messages_override_none_starts_fresh_queue():
    resolved, updated, injections = _run(
        None,
        None,
        [_make_result("skill_c", append_text="首次激活")],
    )
    assert updated[0]["content"] == "首次激活"
    assert resolved.skill_name == "skill_c"
    assert len(injections) == 1