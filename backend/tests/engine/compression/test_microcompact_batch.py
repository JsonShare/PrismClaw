"""批 5：microcompact 双触发批量化 契约测试。

锁定：
1. incremental 语义不变（每轮全量分档表滚动清理）。
2. batch 单轮不触发清理，仅增长累计计数器。
3. batch 计数超预算后触发一次清理，且计数器归零。
4. 历史压缩事件边界复位计数器并把探针快照设为当前全量。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from prismclaw.engine.compression import orchestrator as orch
from prismclaw.engine.compression.history_compaction import CompactionOutcome
from prismclaw.engine.context import microcompact as mc
from prismclaw.schema.session.cache_types import CompressionRuntimeState


def _read_messages(n: int) -> list[dict]:
    """构造 n 组 assistant(tool_use=read) + tool_result 对。"""
    messages: list[dict] = []
    for i in range(n):
        messages.append({
            "role": "assistant",
            "tool_calls": [
                {"id": f"r{i}", "function": {"name": "read", "arguments": "{}"}}
            ],
        })
        messages.append({
            "role": "tool",
            "tool_call_id": f"r{i}",
            "content": f"content-{i}",
        })
    return messages


def _tool_result_chars(messages: list[dict]) -> int:
    return sum(len(m["content"]) for m in messages if m["role"] == "tool")


# ── 1. incremental 语义不变 ──────────────────────────────────────────────

def test_incremental_mode_per_turn_rolling_cleanup():
    # read 分档 keep_recent=8；10 条结果 → 最旧 2 条被清除。
    msgs = _read_messages(10)
    result = mc.microcompact_query_messages(list(msgs))
    assert result.changed is True
    assert result.cleared_count == 2
    # 顺序不重排（append-only 前缀缓存性质）
    assert [m["tool_call_id"] for m in result.messages if m["role"] == "tool"] == [
        f"r{i}" for i in range(10)
    ]
    # 最旧一条已被替换为清除标记
    assert mc._CLEARED_TAG_PREFIX in str(result.messages[1]["content"])


# ── 2/3. batch 单轮计数、超预算触发并归零 ────────────────────────────────

def test_batch_single_turn_does_not_cleanup_only_accumulates():
    msgs = _read_messages(10)
    new_counter, new_total, result = mc.batch_microcompact_tick(
        messages=list(msgs), counter=0, prev_total=0, threshold=10_000_000
    )
    assert result is None
    assert new_counter == _tool_result_chars(msgs)
    assert new_total == _tool_result_chars(msgs)


def test_batch_tick_counts_only_delta():
    """上一轮已有快照时，计数器只增长「新增」字符（保留段不重复计数）。"""
    msgs = _read_messages(10)
    total = _tool_result_chars(msgs)
    new_counter, new_total, result = mc.batch_microcompact_tick(
        messages=list(msgs), counter=0, prev_total=total, threshold=10_000_000
    )
    assert result is None
    assert new_counter == 0  # 无新增
    assert new_total == total


def test_batch_exceeding_budget_triggers_cleanup_and_resets_counter():
    msgs = _read_messages(10)
    threshold = 10  # 首轮必超
    new_counter, new_total, result = mc.batch_microcompact_tick(
        messages=list(msgs), counter=0, prev_total=0, threshold=threshold
    )
    assert result is not None
    assert result.changed is True
    assert result.cleared_count == 2
    assert new_counter == 0


def test_batch_threshold_sources():
    # aggregate_char_budget 优先
    budget = SimpleNamespace(dynamic_zone_tokens=12_288)
    tool_results = SimpleNamespace(aggregate_char_budget=30_000)
    assert mc.batch_cleanup_threshold(budget=budget, tool_results=tool_results) == 30_000

    # aggregate_char_budget 缺省 → 回退 dynamic_zone_tokens×4（字符口径）
    tool_results_none = SimpleNamespace(aggregate_char_budget=None)
    assert (
        mc.batch_cleanup_threshold(budget=budget, tool_results=tool_results_none)
        == 12_288 * 4
    )


# ── 4. 历史压缩边界复位计数器并快照探针 ─────────────────────────────────

def test_compaction_boundary_resets_counter_and_snapshots_probe(monkeypatch):
    """压缩边界不做投影清理（压缩已从树重建历史，投影清理冗余），只复位
    计数器，并把探针快照设为当前全量结果字符——否则下轮 tick 把全量
    误判为新增而立即误触发一次清理。"""
    monkeypatch.setattr(mc, "resolve_microcompact_mode", lambda: "batch")

    msgs = [{"role": "tool", "tool_call_id": "t1", "content": "old-result"}]
    qls = SimpleNamespace(
        query_view=SimpleNamespace(
            messages_for_query=SimpleNamespace(messages=msgs)
        )
    )
    runtime = CompressionRuntimeState(
        tool_result_chars_since_cleanup=123, tool_result_chars_probe=999
    )

    orch._microcompact_at_compaction_boundary(qls, runtime)

    assert runtime.tool_result_chars_since_cleanup == 0
    assert runtime.tool_result_chars_probe == len("old-result")


def test_compaction_boundary_noop_when_incremental(monkeypatch):
    monkeypatch.setattr(mc, "resolve_microcompact_mode", lambda: "incremental")

    qls = SimpleNamespace(query_view=SimpleNamespace(messages_for_query=SimpleNamespace(messages=[])))
    runtime = CompressionRuntimeState(tool_result_chars_since_cleanup=7)

    orch._microcompact_at_compaction_boundary(qls, runtime)

    # incremental 模式完全 no-op：计数器保持原值（该模式不累计计数）。
    assert runtime.tool_result_chars_since_cleanup == 7


@pytest.mark.asyncio
async def test_prepare_turn_compaction_branch_calls_boundary_hook(monkeypatch):
    """压缩分支 outcome.compacted 时调用边界钩子。"""
    spy: list[bool] = []
    monkeypatch.setattr(
        orch,
        "_microcompact_at_compaction_boundary",
        lambda qls, runtime: spy.append(True),
    )

    class _Preflight:
        task_profile = None
        history_tokens = 999_999
        packed_request = None

    async def _prepare_turn_callable(**kwargs):
        return _Preflight()

    async def _maybe_pretrim(**kwargs):
        return CompactionOutcome(ok=True, compacted=True, result=None)

    orchestrator = orch.CompressionOrchestrator(
        prepare_turn_callable=_prepare_turn_callable,
        maybe_pretrim_callable=_maybe_pretrim,
    )

    class _Budget:
        mode = "absolute"
        window_tokens = 131_072

    deps = SimpleNamespace(
        config=SimpleNamespace(context=SimpleNamespace(budget=_Budget()))
    )
    static_result = SimpleNamespace(history_budget=50_000)
    qls = SimpleNamespace(
        query_view=SimpleNamespace(messages_for_query=SimpleNamespace(messages=[]))
    )
    runtime = CompressionRuntimeState()

    await orchestrator.prepare_turn(
        request=SimpleNamespace(),
        deps=deps,
        tree=SimpleNamespace(),
        static_result=static_result,
        attachment_guidance=None,
        query_loop_state=qls,
        compression_runtime=runtime,
        model="test",
        abort_event=None,
        turn_state=None,
        model_max_output=None,
    )

    assert spy == [True]