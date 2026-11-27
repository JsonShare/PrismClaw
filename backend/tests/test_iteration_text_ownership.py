"""锁定「正文 = 终态轮，其余 = timeline」的文本归属规则。

规则（见 specs/body-terminal-turn-only/spec.md）：
- 轮次在下一轮 LLM 开始时即结束 ⇒ 其文本是过程过语，进 timeline；
- 运行结束时仍未结束、且没有工具调用的轮次是终态轮 ⇒ 文本只属于正文；
- 有工具调用的轮次一定不是终态轮。
"""

from __future__ import annotations

from types import SimpleNamespace

from prismclaw.engine.trajectory.timeline import TaskExecutionProjector
from prismclaw.schema.content.streaming import IterationBoundary


def _boundary(iteration: int) -> IterationBoundary:
    return IterationBoundary(iteration=iteration, kind="llm_start")


def _tool_start(call_id: str, name: str, arguments: dict) -> SimpleNamespace:
    return SimpleNamespace(tool_call_id=call_id, step_id=None, name=name, arguments=arguments)


def _tool_end(call_id: str, text: str) -> SimpleNamespace:
    result = SimpleNamespace(is_error=False, content=[SimpleNamespace(text=text)])
    return SimpleNamespace(tool_call_id=call_id, result=result)


def _texts(nodes: list) -> dict[int, str]:
    out: dict[int, str] = {}
    for n in nodes:
        for it in getattr(n, "iterations", []) or []:
            if it.text:
                out[it.segment_id] = it.text
    return out


def test_text_stays_with_its_tool_segment() -> None:
    p = TaskExecutionProjector(workspace_root=None)
    p.on_iteration_boundary(_boundary(1))
    p.on_iteration_text("先看下目录")
    p.on_tool_call_start(_tool_start("c1", "list_dir", {}))
    assert _texts(p.to_nodes()) == {1: "先看下目录"}


def test_open_text_segment_is_answer_candidate_not_in_timeline() -> None:
    p = TaskExecutionProjector(workspace_root=None)
    p.on_iteration_boundary(_boundary(1))
    p.on_iteration_text("最终答复")
    assert _texts(p.to_nodes()) == {}
    assert p.has_content() is False


def test_superseded_text_segment_moves_to_timeline() -> None:
    p = TaskExecutionProjector(workspace_root=None)
    p.on_iteration_boundary(_boundary(1))
    p.on_iteration_text("过程说明")
    p.on_iteration_boundary(_boundary(2))
    p.on_iteration_text("最终答复")
    assert _texts(p.to_nodes()) == {1: "过程说明"}
    assert p.has_content() is True


def test_last_tool_segment_text_is_not_cleared() -> None:
    """回归：曾用 max(segmentId) 事后清空 text，把最后一轮从 timeline 抹掉。"""
    p = TaskExecutionProjector(workspace_root=None)
    p.on_iteration_boundary(_boundary(1))
    p.on_iteration_text("调用工具前说明")
    p.on_tool_call_start(_tool_start("c1", "read", {"file_path": "/tmp/a.txt"}))
    p.on_tool_call_end(_tool_end("c1", "ok"))
    assert _texts(p.to_nodes()) == {1: "调用工具前说明"}
