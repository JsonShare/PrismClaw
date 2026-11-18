"""子代理任务投递与失败识别的契约测试。"""

from prismclaw.engine.subagent.core._lifecycle_mixin import (
    _SYNTHESIZED_RESULT_BUDGET,
    _synthesize_tool_results,
)
from prismclaw.engine.subagent.runtime.follow_up import (
    SessionFollowUpSnapshot,
    build_session_follow_up_note,
    session_follow_up_snapshot,
)
from prismclaw.engine.subagent.runtime.parallel import (
    CURRENT_PARALLEL_TASK_SCHEMA_VERSION,
    PARALLEL_TASK_RUNTIME_CUSTOM_TYPE,
)
from prismclaw.schema.agent.session import MessageEntry
from prismclaw.schema.session.types import CustomEntry


def _tool_messages(count: int, size: int) -> list[dict]:
    messages: list[dict] = []
    for index in range(count):
        call_id = f"call_{index}"
        messages.append(
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": call_id,
                        "function": {"name": "read", "arguments": "{}"},
                    }
                ],
            }
        )
        messages.append(
            {
                "role": "tool",
                "tool_call_id": call_id,
                "content": "x" * size,
            }
        )
    return messages


def test_synthesized_text_never_exceeds_budget() -> None:
    text = _synthesize_tool_results(_tool_messages(20, 1000))
    assert len(text) <= _SYNTHESIZED_RESULT_BUDGET
    assert "[已省略" in text


def test_follow_up_note_uses_record_kind() -> None:
    summary = {"task_count": 2, "completed_count": 1, "latest_task_id": "pt:abc"}
    subagent_note = build_session_follow_up_note(
        SessionFollowUpSnapshot(
            parallel_task_summary=summary,
            parallel_task_attention={"kind": "subagent", "recommended_task_id": "pt:abc"},
            follow_up_attention={"kind": "subagent", "recommended_task_id": "pt:abc"},
        ),
        available_tools=set(),
    )
    assert subagent_note and "后台并行子任务" in subagent_note[0]

    fork_note = build_session_follow_up_note(
        SessionFollowUpSnapshot(
            parallel_task_summary=summary,
            parallel_task_attention={"kind": "fork", "recommended_task_id": "pt:abc"},
            follow_up_attention={"kind": "fork", "recommended_task_id": "pt:abc"},
        ),
        available_tools=set(),
    )
    assert fork_note and "并行 fork 状态" in fork_note[0]


def test_parallel_task_custom_entry_accepts_runtime_type() -> None:
    """真实存储路径会构造 CustomEntry；custom_type 必须在联合类型内。"""
    entry = CustomEntry(
        id="e1",
        parent_id=None,
        custom_type=PARALLEL_TASK_RUNTIME_CUSTOM_TYPE,
        data={"task_id": "pt:1"},
    )
    assert entry.custom_type == "parallel_task_runtime"


class _FakeTree:
    def __init__(self, entries: list) -> None:
        self._entries = entries

    def get_branch(self) -> list:
        return list(self._entries)


def _assistant_entry(entry_id: str = "m1") -> MessageEntry:
    return MessageEntry(id=entry_id, parent_id=None, role="assistant", content="ok")


def _record_entry(*, status: str, entry_id: str = "r1") -> CustomEntry:
    return CustomEntry(
        id=entry_id,
        parent_id=None,
        custom_type=PARALLEL_TASK_RUNTIME_CUSTOM_TYPE,
        data={
            "schema_version": CURRENT_PARALLEL_TASK_SCHEMA_VERSION,
            "task_id": "pt:1",
            "agent_id": "ag:1",
            "owner_session_id": "sess-1",
            "kind": "subagent",
            "status": status,
        },
    )


def test_attention_only_surfaces_new_or_active_records() -> None:
    # 记录出现在最后一条 assistant 消息之后 → 需要提示（后台完成）
    newer = session_follow_up_snapshot(
        _FakeTree([_assistant_entry(), _record_entry(status="completed")])
    )
    assert newer.parallel_task_attention is not None

    # 记录已被 assistant 回复覆盖 → 不再重复提示
    replied = session_follow_up_snapshot(
        _FakeTree([_record_entry(status="completed"), _assistant_entry()])
    )
    assert replied.parallel_task_attention is None

    # 活跃任务即使已被回复覆盖，也要持续提示
    active = session_follow_up_snapshot(
        _FakeTree([_record_entry(status="running"), _assistant_entry()])
    )
    assert active.parallel_task_attention is not None
