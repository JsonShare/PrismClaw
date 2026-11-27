"""Regression tests for the path-B streaming refactor.

Locks the two riskiest contract changes:
  - I3: reload body == last non-empty assistant turn (final answer),
    intermediate turns with tool_calls are timeline-only.

Doom-loop guard tests moved to test_doom_loop_guard.py (record/evaluate
two-phase contract).
"""

from __future__ import annotations

from types import SimpleNamespace

from prismclaw.app.web.routers._message_helpers import _build_display_messages
from prismclaw.schema.agent.session import MessageEntry


def _tree(entries: dict[str, MessageEntry], order: list[str]) -> SimpleNamespace:
    return SimpleNamespace(entries=entries, order=order)


def test_reload_body_is_last_nonempty_turn():
    """正文蓝框只取最后一条非空 assistant content。
    
    中间迭代的过语（a1）已通过 timeline projector 收集并在灰框展示，
    不应再拼进正文蓝框。终态轮次 a2 的完整答复是正文的唯一来源。
    """
    entries = {
        "u1": MessageEntry(id="u1", parent_id=None, role="user", content="请帮我查一下北京天气"),
        "a1": MessageEntry(
            id="a1",
            parent_id="u1",
            role="assistant",
            content="我先检索一下天气资料。",
            tool_calls=[
                {
                    "id": "c1",
                    "function": {"name": "read", "arguments": '{"file_path":"weather.txt"}'},
                }
            ],
        ),
        "t1": MessageEntry(
            id="t1", parent_id="a1", role="tool", content="晴 25°C", tool_call_id="c1"
        ),
        "a2": MessageEntry(
            id="a2",
            parent_id="t1",
            role="assistant",
            content="根据检索结果，北京今天晴，25°C。",
        ),
    }
    tree = _tree(entries, ["u1", "a1", "t1", "a2"])
    msgs = _build_display_messages(tree, review_utils=None)

    assistants = [m for m in msgs if m["role"] == "assistant"]
    assert len(assistants) == 1
    body = assistants[0]["content"]
    # 正文只显示最后一轮非空答复（a2），不拼中间过语（a1）
    assert body == "根据检索结果，北京今天晴，25°C。"
    # Tool calls 保留在 payload（a1 的工具调用）
    assert assistants[0]["tool_calls"][0]["name"] == "read"


def test_multiple_hitl_steps_keep_one_assistant_bubble():
    """同一用户提问中的多轮 HITL 只是执行步骤，不得切成多个回答气泡。

    用户答案只落在 ask 工具结果里；所有问答与后续正文属于同一轮执行。
    最终展示必须是一条 user + 一条 assistant，正文取最后的完整答复，
    各次 ask 工具调用及答案仍保留在该 assistant 的执行数据中。
    """
    entries = {
        "u1": MessageEntry(id="u1", parent_id=None, role="user", content="请处理这份文档"),
        "a1": MessageEntry(
            id="a1",
            parent_id="u1",
            role="assistant",
            content="先确认处理方向。",
            tool_calls=[
                {
                    "id": "c1",
                    "function": {
                        "name": "ask_user_question",
                        "arguments": '{"questions":[{"question":"如何处理？","header":"处理"}]}',
                    },
                }
            ],
        ),
        "t1": MessageEntry(
            id="t1",
            parent_id="a1",
            role="tool",
            content="按排期处理",
            tool_call_id="c1",
            tool_name="ask_user_question",
        ),
        "a2": MessageEntry(
            id="a2",
            parent_id="t1",
            role="assistant",
            content="再确认输出格式。",
            tool_calls=[
                {
                    "id": "c2",
                    "function": {
                        "name": "ask_user_question",
                        "arguments": '{"questions":[{"question":"输出什么格式？","header":"格式"}]}',
                    },
                }
            ],
        ),
        "t2": MessageEntry(
            id="t2",
            parent_id="a2",
            role="tool",
            content="输出 Markdown",
            tool_call_id="c2",
            tool_name="ask_user_question",
        ),
        "a3": MessageEntry(
            id="a3", parent_id="t2", role="assistant", content="已按排期处理并输出 Markdown。"
        ),
    }
    tree = _tree(entries, ["u1", "a1", "t1", "a2", "t2", "a3"])
    msgs = _build_display_messages(tree, review_utils=None)

    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assistant = msgs[1]
    assert assistant["id"] == "a3"
    assert assistant["content"] == "已按排期处理并输出 Markdown。"
    assert [tc["id"] for tc in assistant["tool_calls"]] == ["c1", "c2"]
    assert [tc["result"] for tc in assistant["tool_calls"]] == ["按排期处理", "输出 Markdown"]


def test_legacy_hitl_answer_does_not_create_user_turn():
    """旧会话遗留的 clarification_answer user entry 也只作执行步骤回放。"""
    entries = {
        "u1": MessageEntry(id="u1", parent_id=None, role="user", content="请处理这份文档"),
        "a1": MessageEntry(
            id="a1",
            parent_id="u1",
            role="assistant",
            content="请选择处理方式。",
            tool_calls=[
                {
                    "id": "c1",
                    "function": {"name": "ask_user_question", "arguments": "{}"},
                }
            ],
        ),
        "t1": MessageEntry(
            id="t1",
            parent_id="a1",
            role="tool",
            content="按排期处理",
            tool_call_id="c1",
            tool_name="ask_user_question",
        ),
        "u2": MessageEntry(
            id="u2",
            parent_id="t1",
            role="user",
            content="按排期处理",
            metadata={"kind": "clarification_answer", "ask_tool_call_id": "c1"},
        ),
        "a2": MessageEntry(
            id="a2", parent_id="u2", role="assistant", content="已按排期处理，请查收。"
        ),
    }
    tree = _tree(entries, ["u1", "a1", "t1", "u2", "a2"])
    msgs = _build_display_messages(tree, review_utils=None)

    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["id"] == "u1"
    assert msgs[1]["id"] == "a2"
    assert msgs[1]["content"] == "已按排期处理，请查收。"


async def test_clarification_answer_stays_in_tool_result(monkeypatch):
    """HITL 答案只作为 ask 工具结果落树，不再追加独立 user entry。

    人机交互是执行步骤的一部分；若额外落一条 role=user，会被 turn 计数、
    轨迹分组和客户端渲染当成一次新的用户轮次。
    """
    import prismclaw.engine.execution.dispatch.response_handling as response_handling
    import prismclaw.engine.execution.loop.tree_writer as tree_writer
    from prismclaw.engine.execution.loop._loop_helpers import _persist_clarified_turn

    appended: list = []

    async def fake_append_assistant_response(**kwargs):
        appended.append(("assistant", kwargs.get("tool_calls")))

    async def fake_append(deps, tree, entry):
        appended.append(entry)

    monkeypatch.setattr(
        response_handling, "append_assistant_response", fake_append_assistant_response
    )
    monkeypatch.setattr(tree_writer, "_append", fake_append)

    tree = SimpleNamespace(entries={}, order=[], leaf_id="leaf", header=SimpleNamespace(id="s1"))
    call = SimpleNamespace(call_id="c1", name="ask_user_question", call={"id": "c1"})

    await _persist_clarified_turn(
        deps=SimpleNamespace(),
        tree=tree,
        response=SimpleNamespace(text="", tool_calls=[]),
        allowed_calls=[call],
        ask_call_id="c1",
        answer="方案 B",
    )

    roles = [item[0] if isinstance(item, tuple) else item.role for item in appended]
    assert "user" not in roles
    tool_entries = [
        item for item in appended if not isinstance(item, tuple) and item.role == "tool"
    ]
    assert len(tool_entries) == 1
    assert "方案 B" in str(tool_entries[0].content)


def test_normal_user_message_still_emitted():
    """回归护栏：普通 user 消息必须照常成气泡，不能被其他逻辑误伤。"""
    entries = {
        "u1": MessageEntry(id="u1", parent_id=None, role="user", content="普通提问"),
        "a1": MessageEntry(id="a1", parent_id="u1", role="assistant", content="好的"),
    }
    tree = _tree(entries, ["u1", "a1"])
    msgs = _build_display_messages(tree, review_utils=None)
    user_msgs = [m for m in msgs if m["role"] == "user"]
    assert len(user_msgs) == 1
    assert user_msgs[0]["id"] == "u1"
