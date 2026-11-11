"""T1-5 闭环验收（SDD D9）：无归因环境的 validation ledger 影子路径。

根因回归：attribution_enabled=False 时验证信号 / use_count 生产者整体失活，
curator 毕业断链。现在 ledger 独立于归因开关，本测试锁定：
1. ledger 注入簿记取走语义与去重；
2. aftermath 影子路径产出 task-outcome 信号 + use_count 回写；
3. _scan_task_outcome 的三种判定。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from prismclaw.engine.memory.validation_signals import ValidationSignalLedger
from prismclaw.runtime.memory_aftermath import DefaultMemoryAftermathBoundary
from prismclaw.schema.agent.session import MessageEntry


def _msg(entry_id: str, role: str, *, is_error: bool = False) -> MessageEntry:
    return MessageEntry(
        id=entry_id, parent_id=None, type="message", role=role,  # type: ignore[arg-type]
        content="x", is_error=is_error,
    )


class _FakeStore:
    def __init__(self) -> None:
        self.validation_events: list[dict[str, Any]] = []
        self.use_bumped: list[list[str]] = []

    async def record_validation_events(
        self, session_key: str, events: list[dict[str, Any]]
    ) -> int:
        self.validation_events.extend(events)
        return len(events)

    async def writeback_use_counts(self, session_key: str, entry_ids: list[str]) -> int:
        self.use_bumped.append(list(entry_ids))
        return len(entry_ids)


def test_ledger_note_take_dedup() -> None:
    ledger = ValidationSignalLedger(_FakeStore())
    ledger.note_injections("s1", ["a", "b", "a"])
    assert ledger.take_pending("s1") == ["a", "b"]
    assert ledger.take_pending("s1") == []  # 取走即清


@pytest.mark.asyncio
async def test_aftermath_shadow_path_produces_signals_and_use_counts() -> None:
    store = _FakeStore()
    ledger = ValidationSignalLedger(store)
    ledger.note_injections("web:test", ["m1", "m2"])

    branch: list[MessageEntry] = [
        _msg("u1", "user"),
        _msg("t1", "tool"),            # 成功工具
        _msg("a1", "assistant"),       # 有回复 → task_success
    ]
    fake_tree = SimpleNamespace(get_branch=lambda: branch)

    class _FakeSessions:
        async def load(self, session_id: str):
            return fake_tree

    boundary = DefaultMemoryAftermathBoundary(
        memory_store=store,
        session_store=_FakeSessions(),
        attribution_recorder=None,     # ← 无归因环境（生产默认）
        validation_ledger=ledger,
    )
    await boundary._record_attribution_signals("web:test")

    assert len(store.validation_events) == 2
    assert all(e["polarity"] == "positive" for e in store.validation_events)
    assert all(e["signal_type"] == "task_success" for e in store.validation_events)
    assert all(e["attribution"] == "weak" for e in store.validation_events)
    assert store.use_bumped == [["m1", "m2"]]
    assert ledger.take_pending("web:test") == []  # 影子路径后 pending 清空


@pytest.mark.asyncio
async def test_aftermath_no_pending_no_signal() -> None:
    store = _FakeStore()
    ledger = ValidationSignalLedger(store)
    branch: list[MessageEntry] = [_msg("u1", "user"), _msg("t1", "tool"), _msg("a1", "assistant")]
    fake_tree = SimpleNamespace(get_branch=lambda: branch)

    class _FakeSessions:
        async def load(self, session_id: str):
            return fake_tree

    boundary = DefaultMemoryAftermathBoundary(
        memory_store=store,
        session_store=_FakeSessions(),
        validation_ledger=ledger,
    )
    await boundary._record_attribution_signals("web:test")
    assert store.validation_events == []
    assert store.use_bumped == []


def test_scan_task_outcome_three_branches() -> None:
    scan = DefaultMemoryAftermathBoundary._scan_task_outcome
    ok_branch = [_msg("u", "user"), _msg("t", "tool"), _msg("a", "assistant")]
    err_branch = [_msg("u", "user"), _msg("t", "tool", is_error=True)]
    chat_branch = [_msg("u", "user"), _msg("a", "assistant")]
    assert scan(ok_branch, 0, ["x"]) == ("task_success", "positive")
    assert scan(err_branch, 0, ["x"]) == ("task_failure", "negative")
    assert scan(chat_branch, 0, ["x"]) is None
    assert scan(ok_branch, 0, []) is None