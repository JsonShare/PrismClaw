"""子代理进度「最近动作」契约测试：有界、去噪、跨轮追加、终态不追加。"""

import asyncio

from prismclaw.engine.subagent.core._loop_mixin import LoopMixin
from prismclaw.engine.toolkit.subagent.subagent_progress_emitter import (
    SubagentProgressEmitter,
)
from prismclaw.schema.content.streaming import SubagentProgressBatch, SubagentProgressItem


def _item(
    *,
    tool: str | None,
    turn: int = 1,
    summary: str | None = None,
    status: str = "tool_running",
) -> SubagentProgressItem:
    return SubagentProgressItem(
        sub_session_id="sub-1",
        subagent_name="scout",
        turn=turn,
        max_turns=5,
        partial_status=status,
        current_tool=tool,
        current_tool_summary=summary,
        duration_so_far=1.0,
    )


def _emitter() -> SubagentProgressEmitter:
    return SubagentProgressEmitter(progress_batch_id="batch-1", sink=None)


def test_first_tool_appends_activity():
    emitter = _emitter()
    emitter.update(_item(tool="read_file", summary="file_path=a.ts"))
    assert emitter._states["sub-1"].recent_activity == ["read_file file_path=a.ts"]


def test_same_tool_same_turn_is_deduplicated():
    emitter = _emitter()
    emitter.update(_item(tool="grep", summary="pattern=TODO"))
    emitter.update(_item(tool="grep", summary="pattern=TODO"))
    emitter.update(_item(tool="grep", summary="pattern=TODO", turn=1))
    assert emitter._states["sub-1"].recent_activity == ["grep pattern=TODO"]


def test_same_tool_same_summary_next_turn_is_deduplicated():
    """跨轮次重复同一动作没有新信息，不追加（避免噪声行）。"""
    emitter = _emitter()
    emitter.update(_item(tool="bash", summary="command=ls", turn=1))
    emitter.update(_item(tool="bash", summary="command=ls", turn=2))
    assert emitter._states["sub-1"].recent_activity == ["bash command=ls"]


def test_same_tool_new_summary_next_turn_appends():
    emitter = _emitter()
    emitter.update(_item(tool="grep", summary="pattern=TODO", turn=1))
    emitter.update(_item(tool="grep", summary="pattern=FIXME", turn=2))
    assert emitter._states["sub-1"].recent_activity == [
        "grep pattern=TODO",
        "grep pattern=FIXME",
    ]


def test_activity_is_bounded_to_last_five():
    emitter = _emitter()
    for index in range(7):
        emitter.update(_item(tool=f"tool_{index}", turn=index + 1))
    activity = emitter._states["sub-1"].recent_activity
    assert activity == [f"tool_{index}" for index in range(2, 7)]


def test_terminal_update_does_not_append():
    emitter = _emitter()
    emitter.update(_item(tool="read_file", summary="file_path=a.ts"))
    emitter.update(_item(tool=None, status="completed"))
    assert emitter._states["sub-1"].recent_activity == ["read_file file_path=a.ts"]
    assert emitter._states["sub-1"].partial_status == "completed"


def test_flush_emits_activity_in_batch():
    batches: list[SubagentProgressBatch] = []

    async def sink(batch: SubagentProgressBatch) -> None:
        batches.append(batch)

    emitter = SubagentProgressEmitter(progress_batch_id="batch-1", sink=sink)
    emitter.update(_item(tool="read_file", summary="file_path=a.ts"))
    asyncio.run(emitter.flush_final())

    assert len(batches) == 1
    assert batches[0].items[0].recent_activity == ["read_file file_path=a.ts"]


def test_tool_summary_picks_known_argument():
    assert (
        LoopMixin._summarize_tool_call(
            [{"function": {"name": "read_file", "arguments": '{"file_path": "src/app.ts"}'}}]
        )
        == "file_path=src/app.ts"
    )
    assert (
        LoopMixin._summarize_tool_call(
            [{"function": {"name": "bash", "arguments": {"command": "npm run build"}}}]
        )
        == "command=npm run build"
    )


def test_tool_summary_is_none_on_bad_input():
    assert LoopMixin._summarize_tool_call([]) is None
    assert LoopMixin._summarize_tool_call([{"function": {"arguments": "{not json"}}]) is None
    assert LoopMixin._summarize_tool_call([{"function": {"arguments": {"x": 1}}}]) is None
