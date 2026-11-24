"""todo_write 工具校验与回执契约，以及"工具写入 → 状态 → 下一轮渲染"闭环。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from prismclaw.engine.compression.state_store import SessionStateStore
from prismclaw.engine.toolkit.misc.todo_write import TodoWriteTool
from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext
from prismclaw.schema.agent.session import SessionContextState


def _context() -> ToolContext:
    return ToolContext(
        workspace_id="w",
        workspace_path=Path("/tmp"),
        session_state=SessionContextState(session_id="s"),
    )


def _run(tool: TodoWriteTool, todos: list[dict[str, Any]], ctx: ToolContext):
    return asyncio.run(tool.execute({"todos": todos, "_call_id": "c1"}, ctx))


def _three_todos() -> list[dict[str, Any]]:
    return [
        {"id": "1", "content": "收集资料", "status": "completed"},
        {"id": "2", "content": "撰写初稿", "status": "in_progress"},
        {"id": "3", "content": "校对发布", "status": "pending"},
    ]


def test_success_writes_state_and_names_current_step() -> None:
    ctx = _context()
    result = _run(TodoWriteTool(), _three_todos(), ctx)
    assert not result.is_error, f"合法清单不应报错：{result.content[0].text}"
    text = result.content[0].text
    assert "当前步骤：2" in text, f"回执应点名进行中步骤 id=2，实际：{text!r}"
    assert "撰写初稿" in text, f"回执应带进行中步骤内容，实际：{text!r}"
    assert len(ctx.session_state.todo_list) == 3, "成功调用必须把清单写入 session_state"


def test_zero_in_progress_allowed() -> None:
    ctx = _context()
    todos = [
        {"id": "1", "content": "第一步", "status": "pending"},
        {"id": "2", "content": "第二步", "status": "pending"},
    ]
    result = _run(TodoWriteTool(), todos, ctx)
    assert not result.is_error, f"无进行中项应合法：{result.content[0].text}"
    assert "当前无进行中步骤" in result.content[0].text, (
        f"回执应说明没有进行中步骤，实际：{result.content[0].text!r}"
    )


def test_duplicate_id_rejected() -> None:
    ctx = _context()
    todos = [
        {"id": "dup", "content": "甲", "status": "pending"},
        {"id": "dup", "content": "乙", "status": "pending"},
    ]
    result = _run(TodoWriteTool(), todos, ctx)
    assert result.is_error, "重复 id 必须被拒绝"
    assert "id 必须唯一" in result.content[0].text, f"错误文本不符：{result.content[0].text!r}"
    assert ctx.session_state.todo_list == [], "校验失败不得写入 session_state"


def test_multiple_in_progress_rejected() -> None:
    ctx = _context()
    todos = [
        {"id": "1", "content": "甲", "status": "in_progress"},
        {"id": "2", "content": "乙", "status": "in_progress"},
    ]
    result = _run(TodoWriteTool(), todos, ctx)
    assert result.is_error, "多个 in_progress 必须被拒绝"
    assert "同一时刻只能有一个 in_progress" in result.content[0].text, (
        f"错误文本不符：{result.content[0].text!r}"
    )
    assert ctx.session_state.todo_list == [], "校验失败不得写入 session_state"


def test_more_than_20_rejected() -> None:
    ctx = _context()
    todos = [{"id": str(i), "content": f"步骤{i}", "status": "pending"} for i in range(1, 22)]
    result = _run(TodoWriteTool(), todos, ctx)
    assert result.is_error, "超过 20 项必须被拒绝"
    assert "最多 20 项" in result.content[0].text, f"错误文本不符：{result.content[0].text!r}"
    assert ctx.session_state.todo_list == [], "校验失败不得写入 session_state"


def test_empty_content_rejected() -> None:
    ctx = _context()
    todos = [{"id": "1", "content": "   ", "status": "pending"}]
    result = _run(TodoWriteTool(), todos, ctx)
    assert result.is_error, "空 content 必须被拒绝"
    assert "content 不能为空" in result.content[0].text, f"错误文本不符：{result.content[0].text!r}"
    assert ctx.session_state.todo_list == [], "校验失败不得写入 session_state"


def test_invalid_status_rejected() -> None:
    ctx = _context()
    todos = [{"id": "1", "content": "甲", "status": "done"}]
    result = _run(TodoWriteTool(), todos, ctx)
    assert result.is_error, "非法 status 必须被拒绝"
    assert "无效的待办条目" in result.content[0].text, f"错误文本不符：{result.content[0].text!r}"
    assert ctx.session_state.todo_list == [], "校验失败不得写入 session_state"


def test_tool_write_appears_in_next_turn_block() -> None:
    ctx = _context()
    result = _run(TodoWriteTool(), _three_todos(), ctx)
    assert not result.is_error, f"写入应成功：{result.content[0].text}"

    store = SessionStateStore()
    store.hydrate_state("s", ctx.session_state)
    block = store.build_context_block("s")
    assert block is not None, "写入清单后 volatile 块必须存在"
    assert "<todos>" in block.content, f"下一轮块缺少清单：{block.content!r}"
    assert "[~] 2/3 撰写初稿" in block.content, f"下一轮块缺少当前步骤：{block.content!r}"
