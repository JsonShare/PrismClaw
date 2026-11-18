"""todo_list 常驻上下文渲染：顺序、不截断、空/全完成收敛、跨回合与恢复。

清单是唯一随每步变化、且必须每轮都出现在 volatile 块里的状态；
这些用例锁定渲染契约，防止重构把清单退回"仅首轮出现"的行为。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import patch

from prismclaw.engine.compression.assemble_coordinator import AssembleCoordinator
from prismclaw.engine.compression.history_compaction import (
    _persist_session_memory_snapshot_for_compaction,
)
from prismclaw.engine.compression.state_store import SessionStateStore
from prismclaw.engine.compression.task_profile_inference import TaskProfileInference
from prismclaw.engine.state.state_protocol import (
    diff_state_payload,
    render_state_payload_block,
    session_memory_payload_from_state,
    state_payload_from_state,
)
from prismclaw.engine.toolkit.misc.todo_write import TodoWriteTool
from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext
from prismclaw.schema.agent.session import SessionContextState, TodoItem
from prismclaw.schema.content.compaction import CompactResult
from prismclaw.schema.content.evidence import EvidenceStrategy
from prismclaw.schema.session.types import (
    SESSION_MEMORY_CUSTOM_TYPE,
    CustomEntry,
    SessionHeader,
    SessionTree,
)


def _state(todos: list[TodoItem], *, goal: str | None = None) -> SessionContextState:
    return SessionContextState(session_id="s1", primary_goal=goal, todo_list=todos)


def _three_items() -> list[TodoItem]:
    return [
        TodoItem(id="1", content="收集资料", status="completed"),
        TodoItem(id="2", content="撰写初稿", status="in_progress"),
        TodoItem(id="3", content="校对发布", status="pending"),
    ]


def _render(state: SessionContextState) -> str:
    content = render_state_payload_block(wrapper="session_state", state_payload=state)
    assert content is not None, "含未完成项的清单必须渲染出 session_state 块"
    return content


def test_todo_list_rendered_in_order() -> None:
    content = _render(_state(_three_items()))
    lines = content.splitlines()
    expected = [
        "[x] 1/3 收集资料",
        "[~] 2/3 撰写初稿",
        "[ ] 3/3 校对发布",
    ]
    positions = [lines.index(line) for line in expected]
    assert positions == sorted(positions), f"清单行必须保持输入顺序，实际输出：{content!r}"
    assert "<todos>" in lines, f"清单必须包在 <todos> 块内，实际输出：{content!r}"
    assert "</todos>" in lines, f"清单块必须闭合，实际输出：{content!r}"


def test_no_truncation_at_render() -> None:
    todos = [TodoItem(id=str(i), content=f"步骤{i}", status="pending") for i in range(1, 21)]
    content = _render(_state(todos))
    assert "[ ] 20/20 步骤20" in content, f"渲染不得截断清单，实际输出：{content!r}"


def test_all_completed_returns_none() -> None:
    todos = [
        TodoItem(id="1", content="已完成一", status="completed"),
        TodoItem(id="2", content="已完成二", status="completed"),
    ]
    content = render_state_payload_block(wrapper="session_state", state_payload=_state(todos))
    assert content is None, f"全部完成时不应产生空 wrapper 块，实际输出：{content!r}"


def test_empty_state_returns_none() -> None:
    content = render_state_payload_block(
        wrapper="session_state", state_payload=SessionContextState(session_id="s1")
    )
    assert content is None, f"空 state 不应产生任何块，实际输出：{content!r}"


def test_delta_keeps_full_list_when_unchanged() -> None:
    state = _state(_three_items())
    payload = state_payload_from_state(state)
    delta = diff_state_payload(payload, session_memory_payload_from_state(state))
    assert delta["todo_list"] == payload["todo_list"], "delta 必须输出当前全量清单，不能依赖 baseline"

    delta_content = render_state_payload_block(wrapper="session_state", state_payload=delta)
    assert delta_content is not None, "无变化时渲染 delta 仍必须给出 session_state 块"
    for line in ("[x] 1/3 收集资料", "[~] 2/3 撰写初稿", "[ ] 3/3 校对发布"):
        assert line in delta_content, f"delta 渲染缺少清单行 {line!r}，实际输出：{delta_content!r}"


def test_session_memory_excludes_todo_list() -> None:
    state = _state(_three_items(), goal="交付清单功能")
    assert session_memory_payload_from_state(state)["todo_list"] == [], (
        "session_memory 载荷必须清空 todo_list，避免清单污染 run 级缓存键"
    )

    store = SessionStateStore()
    store.hydrate_state("s1", state)
    block = store.build_persisted_memory_block("s1", baseline_state=state)
    assert block is not None, "有 primary_goal 的基线应产出持久记忆块"
    assert "<todos>" not in block.content, f"持久记忆块不得包含清单，实际内容：{block.content!r}"


def test_turns_and_resume_keep_todos() -> None:
    store = SessionStateStore()
    state = _state(_three_items())
    store.hydrate_state("s1", state)
    baseline = state.model_copy(deep=True)

    first = store.build_context_block("s1", baseline_state=baseline)
    assert first is not None, "第一轮必须产出 volatile 块"
    assert "<todos>" in first.content, f"第一轮块缺少清单：{first.content!r}"
    assert "[~] 2/3" in first.content, f"第一轮块缺少当前步骤：{first.content!r}"

    second = store.build_context_block("s1", baseline_state=baseline)
    assert second is not None, "第二轮必须产出 volatile 块"
    assert "<todos>" in second.content, f"第二轮未变化时清单也不得消失：{second.content!r}"
    assert "[~] 2/3" in second.content, f"第二轮块缺少当前步骤：{second.content!r}"

    resumed_store = SessionStateStore()
    resumed_store.hydrate_state("s1", state)
    resumed = resumed_store.build_context_block("s1")
    assert resumed is not None, "恢复会话后必须产出 volatile 块"
    assert "<todos>" in resumed.content, f"恢复后清单丢失：{resumed.content!r}"
    assert "[ ] 3/3" in resumed.content, f"恢复后未完成项丢失：{resumed.content!r}"


def test_turn_end_rebuild_preserves_todo_list() -> None:
    # 回合结束时 ContextEngine.after_turn 会从历史重建 state；
    # 清单不来自历史消息，重建必须原样保留，否则下一回合就失明。
    store = SessionStateStore()
    store.get_state("s1").todo_list = _three_items()

    store.rebuild_from_messages("s1", [{"role": "user", "content": "继续执行"}])

    assert len(store.get_state("s1").todo_list) == 3, "重建不得清空清单"
    block = store.build_context_block("s1")
    assert block is not None, "重建后 volatile 块应存在"
    assert "[~] 2/3 撰写初稿" in block.content, f"重建后当前步骤丢失：{block.content!r}"


def test_compaction_snapshot_keeps_todo_list() -> None:
    # 压缩快照从当前 state 拷贝、只叠加消息派生的更新，清单必须原样保留。
    state = _state(_three_items(), goal="交付清单功能")
    tree = SessionTree(header=SessionHeader(id="s1", workspace_id="w", agent_id="a"))
    tree.entries["e1"] = CustomEntry(
        id="e1",
        parent_id=None,
        custom_type=SESSION_MEMORY_CUSTOM_TYPE,
        data=state.model_dump(mode="json"),
    )
    tree.leaf_id = "e1"

    class _SessionStore:
        def __init__(self) -> None:
            self.events: list[Any] = []

        async def commit_events(self, events: list[Any]) -> None:
            self.events.extend(events)

    class _Deps:
        def __init__(self) -> None:
            self.session_store = _SessionStore()

    result = CompactResult(
        ok=True,
        compacted=True,
        details={
            "artifact_type": "history_compaction_summary",
            "artifact_id": "a1",
            "scope": "history_summary",
            "summary_text": "摘要",
            "state_updates": {"primary_goal": "更新后的目标", "recent_messages": []},
        },
    )
    asyncio.run(
        _persist_session_memory_snapshot_for_compaction(
            deps=_Deps(),  # type: ignore[arg-type]
            tree=tree,
            session_id="s1",
            compact_result=result,
        )
    )

    latest = tree.get_latest_session_memory_state()
    assert latest is not None, "压缩后必须产出新的记忆快照"
    assert [t.id for t in latest.todo_list] == ["1", "2", "3"], "压缩快照不得丢清单"
    assert latest.todo_list[1].status == "in_progress", "压缩后当前步骤状态必须保留"
    assert latest.primary_goal == "更新后的目标", "压缩产生的更新仍须生效"


class _Retriever:
    interactive_retrieval_budget_s = 3.0

    def build_evidence_strategy(self, **_: Any) -> EvidenceStrategy:
        return EvidenceStrategy(strategy_id="fake", session_id="s1", strategy="none")

    async def retrieve_evidence(self, **_: Any) -> tuple[list[Any], list[Any], list[Any]]:
        return [], [], []


class _MemoryFormatter:
    def format_blocks_from_packets(self, packets: Any, *, graph: Any) -> list[Any]:
        return []


class _ValidatorChain:
    token_budget = None

    async def validate_and_transform(self, memory_plan: Any, prompt: str | None) -> str | None:
        return prompt


class _Ports:
    def get_context_validator_chain(self) -> _ValidatorChain:
        return _ValidatorChain()


def test_assemble_injects_todos_every_request() -> None:
    # 真链路：todo_write 写入 live state → 连续三轮 assemble 的 dynamic 块都带清单。
    store = SessionStateStore()
    ctx = ToolContext(
        workspace_id="w",
        workspace_path=Path("/tmp"),
        session_state=store.get_state("s1"),
    )
    written = asyncio.run(
        TodoWriteTool().execute(
            {
                "todos": [
                    {"id": "1", "content": "收集资料", "status": "completed"},
                    {"id": "2", "content": "撰写初稿", "status": "in_progress"},
                    {"id": "3", "content": "校对发布", "status": "pending"},
                ],
                "_call_id": "c1",
            },
            ctx,
        )
    )
    assert not written.is_error, written.content[0].text

    coordinator = AssembleCoordinator(
        memory_retriever=_Retriever(),  # type: ignore[arg-type]
        state_store=store,
        memory_formatter=_MemoryFormatter(),  # type: ignore[arg-type]
        task_profile_inference=TaskProfileInference(),
    )
    coordinator.set_ports(_Ports())  # type: ignore[arg-type]

    async def _assemble_three() -> list[Any]:
        with patch(
            "prismclaw.engine.compression.assemble_coordinator.build_context_graph",
            return_value=None,
        ):
            return [
                await coordinator.assemble(session_id="s1", messages=[], prompt="继续")
                for _ in range(3)
            ]

    for assembled in asyncio.run(_assemble_three()):
        contents = [block.content or "" for block in assembled.context_layout.dynamic_blocks]
        assert any("<todos>" in content for content in contents), contents
        assert any("[~] 2/3 撰写初稿" in content for content in contents), contents
