"""Regression tests for the path-B streaming refactor.

Locks the two riskiest contract changes:
  - I3: reload body == live full transcript (Claude Code mode).

Doom-loop guard tests moved to test_doom_loop_guard.py (record/evaluate
two-phase contract).
"""
from __future__ import annotations

from types import SimpleNamespace

from prismclaw.app.web.routers._message_helpers import _build_display_messages
from prismclaw.schema.agent.session import MessageEntry


def _tree(entries: dict[str, MessageEntry], order: list[str]) -> SimpleNamespace:
    return SimpleNamespace(entries=entries, order=order)


def test_reload_body_is_full_transcript():
    """Reload must render ALL assistant turns (incl. intermediate drafts) as
    the body, joined with '\\n\\n' — matching the live streamed transcript."""
    entries = {
        "u1": MessageEntry(id="u1", parent_id=None, role="user", content="请帮我查一下北京天气"),
        "a1": MessageEntry(
            id="a1",
            parent_id="u1",
            role="assistant",
            content="我先检索一下天气资料。",
            tool_calls=[{"id": "c1", "function": {"name": "read", "arguments": '{"file_path":"weather.txt"}'}}],
        ),
        "t1": MessageEntry(id="t1", parent_id="a1", role="tool", content="晴 25°C", tool_call_id="c1"),
        "a2": MessageEntry(
            id="a2", parent_id="t1", role="assistant",
            content="根据检索结果，北京今天晴，25°C。",
        ),
    }
    tree = _tree(entries, ["u1", "a1", "t1", "a2"])
    msgs = _build_display_messages(tree, review_utils=None)

    assistants = [m for m in msgs if m["role"] == "assistant"]
    assert len(assistants) == 1
    body = assistants[0]["content"]
    # Both turns present (not just the final answer).
    assert "我先检索一下天气资料。" in body
    assert "根据检索结果，北京今天晴，25°C。" in body
    # Exact join matches live contract ("\n\n".join per-turn parts).
    assert body == "我先检索一下天气资料。\n\n根据检索结果，北京今天晴，25°C。"
    # Tool calls preserved in the payload.
    assert assistants[0]["tool_calls"][0]["name"] == "read"


def test_assistant_bubbles_around_hitl_tool_not_merged():
    """HITL 提问气泡 a1 与其后的回复 a2 必须各自独立成气泡。

    关键回归护栏：若处理 tool 结果时未先 flush 前置的提问气泡 a1，a1 会被
    a2 覆盖而丢失 / 并入 a2。本测试显式构造 [u1, a1, t1, a2] 兜住该回归。
    """
    entries = {
        # 上传记录：普通 user 气泡，必须照常出现
        "u1": MessageEntry(id="u1", parent_id=None, role="user",
                           content=[{"type": "file", "path": "/w/x.docx", "name": "x.docx"}]),
        # 助手提问（AskUserQuestion）
        "a1": MessageEntry(
            id="a1", parent_id="u1", role="assistant", content="请选择处理方式。",
            tool_calls=[{"id": "c1", "function": {"name": "ask_user_question",
                       "arguments": '{"questions":[{"question":"如何处理？","header":"处理"}]}'}}],
        ),
        # 用户作答只作为 ask 工具结果存在，不落独立 user entry
        "t1": MessageEntry(id="t1", parent_id="a1", role="tool",
                           content="排期文档处理：评估排期合理性", tool_call_id="c1"),
        # 答案之后 agent 的后续回复（真实 HITL 流程必有）
        "a2": MessageEntry(id="a2", parent_id="t1", role="assistant",
                           content="已按排期处理，请查收。"),
    }
    tree = _tree(entries, ["u1", "a1", "t1", "a2"])
    msgs = _build_display_messages(tree, review_utils=None)

    user_msgs = [m for m in msgs if m["role"] == "user"]
    assert len(user_msgs) == 1, [m["id"] for m in user_msgs]
    assert user_msgs[0]["id"] == "u1"
    assert "排期文档处理" not in (user_msgs[0].get("content") or "")

    # 提问气泡 a1 与后续回复 a2 必须各自独立成气泡（不能被覆盖/合并/丢失）
    assistant_msgs = [m for m in msgs if m["role"] == "assistant"]
    assert len(assistant_msgs) == 2, [m["id"] for m in assistant_msgs]
    assert {m["id"] for m in assistant_msgs} == {"a1", "a2"}
    # a1 携带 ask_user 工具调用（时间线据此渲染「人机交互」步骤）
    a1 = next(m for m in assistant_msgs if m["id"] == "a1")
    assert any(tc.get("name", "").lower().find("ask") >= 0 for tc in a1.get("tool_calls", []))
    # a2 携带其自身回复正文
    a2 = next(m for m in assistant_msgs if m["id"] == "a2")
    assert "已按排期处理" in (a2.get("content") or "")


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
