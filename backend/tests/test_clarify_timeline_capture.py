"""锁定 HITL 澄清答案进入执行时间线的基石。

「抑制独立气泡后答案仍不丢失」依赖：agent_loop 在用户作答后 yield 的合成
ToolCallEnd 经 chat_runner 落入 projector.on_tool_call_end，把答案写入 ask_user
操作的 output；历史回放时该 output 经 frontend parseAskUserInteraction 渲染为
「人机交互」步骤的 question+answer。本项目测试只验证后端半边（投影器确实把
答案写入 ask_user 操作的 output），前端半边由现有 stepRows/timelineParse 单测覆盖。
"""
from __future__ import annotations

from types import SimpleNamespace

from prismclaw.engine.trajectory.timeline import TaskExecutionProjector


def _tool_start(call_id: str, name: str, arguments: dict) -> SimpleNamespace:
    return SimpleNamespace(tool_call_id=call_id, step_id=None, name=name, arguments=arguments)


def _tool_end(call_id: str, text: str, is_error: bool = False) -> SimpleNamespace:
    result = SimpleNamespace(is_error=is_error, content=[SimpleNamespace(text=text)])
    return SimpleNamespace(tool_call_id=call_id, result=result)


def _find_operation(nodes: list, call_id: str):
    for n in nodes:
        for it in getattr(n, "iterations", []) or []:
            for op in getattr(it, "operations", []) or []:
                if getattr(op, "call_id", None) == call_id:
                    return op
    return None


def test_ask_user_operation_captures_answer_as_output():
    p = TaskExecutionProjector(workspace_root=None)
    p.set_message_id("a1")
    p.on_tool_call_start(
        _tool_start("c1", "ask_user_question",
                   {"questions": [{"question": "如何处理？", "header": "处理"}]}),
    )
    # HITL 用户作答后合成 ToolCallEnd 落 output（与 agent_loop._create_clarify_tool_end 同形态）
    p.on_tool_call_end(_tool_end("c1", "排期文档处理：评估排期合理性"))

    op = _find_operation(p.to_nodes(), "c1")
    assert op is not None, "ask_user 操作未出现在时间线节点"
    assert op.output == "排期文档处理：评估排期合理性"
    assert op.status == "done"
