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
            tool_calls=[{"id": "c1", "function": {"name": "web_search", "arguments": '{"q":"北京天气"}'}}],
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
    assert assistants[0]["tool_calls"][0]["name"] == "web_search"


def test_clarification_answer_not_emitted_as_standalone_bubble():
    """HITL 澄清追问的答案（metadata.kind=="clarification_answer"）不得作为
    独立 user 气泡出现——它已内嵌在提问气泡的执行时间线（ask_user 步骤的
    question+answer），单独成气泡会与上传记录割裂为两条 user 记录。

    关键回归护栏：答案之后通常还有 agent 的后续回复（a2）。若跳过答案节点时
    未先 flush 前置的提问气泡 a1，a1 会被 a2 覆盖而丢失 / 并入 a2。本测试显式
    构造 [u1, a1, t1, u2(答案), a2] 来兜住该回归（此前测试无 a2，假绿）。
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
        "t1": MessageEntry(id="t1", parent_id="a1", role="tool",
                           content="排期文档处理：评估排期合理性", tool_call_id="c1"),
        # 澄清答案：被标记为 clarification_answer，不应成为独立气泡
        "u2": MessageEntry(
            id="u2", parent_id="t1", role="user",
            content="排期文档处理：评估排期合理性",
            metadata={"kind": "clarification_answer", "ask_tool_call_id": "c1"},
        ),
        # 答案之后 agent 的后续回复（真实 HITL 流程必有）
        "a2": MessageEntry(id="a2", parent_id="u2", role="assistant",
                           content="已按排期处理，请查收。"),
    }
    tree = _tree(entries, ["u1", "a1", "t1", "u2", "a2"])
    msgs = _build_display_messages(tree, review_utils=None)

    user_msgs = [m for m in msgs if m["role"] == "user"]
    # 仅上传记录成气泡；澄清答案被抑制（不出现第二条 user 记录）
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


def test_normal_user_message_still_emitted():
    """回归护栏：普通 user 消息（无 clarification_answer 标记）必须照常成气泡，
    上面的抑制逻辑不能误伤正常用户输入。"""
    entries = {
        "u1": MessageEntry(id="u1", parent_id=None, role="user", content="普通提问"),
        "a1": MessageEntry(id="a1", parent_id="u1", role="assistant", content="好的"),
    }
    tree = _tree(entries, ["u1", "a1"])
    msgs = _build_display_messages(tree, review_utils=None)
    user_msgs = [m for m in msgs if m["role"] == "user"]
    assert len(user_msgs) == 1
    assert user_msgs[0]["id"] == "u1"
