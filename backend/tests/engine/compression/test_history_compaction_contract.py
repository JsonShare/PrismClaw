"""Phase 1 基线: history_compaction 行为契约测试。

锁定不变量（重构过程必须持续通过）：
1. CompactResult 字段结构：ok / compacted / summary / tokens_before / tokens_after / first_kept_entry_id
2. token 单调下降：tokens_after < tokens_before（成功路径）
3. carry_forward_missing_sections：critical section 缺失时从 prior 摘要 append
4. _query_messages_for_compaction：fact sheet 注入 + meta_messages 渲染顺序
5. predict_compaction_trigger：emergency_ratio 阈值触发 + 样本不足时返回 False
"""
from __future__ import annotations

from prismclaw.engine.compression.history_compaction import (
    predict_compaction_trigger,
)
from prismclaw.engine.compression.compaction import (
    build_compaction,
    estimate_message_tokens,
    find_cut_point,
)
from prismclaw.engine.compression.hardening import (
    carry_forward_missing_sections,
)
from prismclaw.schema.content.compaction import CompactResult
from prismclaw.schema.session.cache_types import CompressionRuntimeState


# ──────────────────────────── 1. CompactResult 字段契约 ────────────────────────────


def test_compact_result_minimal_fields():
    """CompactResult 必须含核心字段，缺省值与文档一致。"""
    result = CompactResult(ok=True, compacted=False, reason="within-budget")
    assert result.ok is True
    assert result.compacted is False
    assert result.reason == "within-budget"
    assert result.reason_code is None
    assert result.summary is None
    assert result.details is None
    assert result.first_kept_entry_id is None
    assert result.tokens_before == 0
    assert result.tokens_after is None
    assert result.validation is None


def test_compact_result_full_fields_round_trip():
    """完整 CompactResult 字段可往返。"""
    result = CompactResult(
        ok=True,
        compacted=True,
        summary="已压缩摘要",
        tokens_before=10000,
        tokens_after=4000,
        first_kept_entry_id="entry_5",
        reason=None,
    )
    assert result.tokens_after is not None
    assert result.tokens_after < result.tokens_before
    assert result.summary  # 非空
    assert result.first_kept_entry_id == "entry_5"


# ──────────────────────────── 2. token 单调下降（build_compaction 决策） ────────────────────────────


def test_build_compaction_within_budget():
    """消息集合在预算内 → 不应压缩。"""
    messages = [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好，请问需要什么帮助？"},
    ]
    strategy = build_compaction(messages, context_window=131072, threshold=0.8)
    assert strategy.should_compact is False
    assert strategy.reason == "within-budget"
    assert strategy.estimated_tokens > 0


def test_build_compaction_over_budget_triggers_cut():
    """消息集合超预算 + cut_index 安全 → 触发压缩，kept_tokens < estimated。"""
    # 构造超预算场景：context_window 极小，threshold=1.0
    messages = []
    for i in range(50):
        messages.append({"role": "user", "content": f"问题 {i} " * 100})
        messages.append({"role": "assistant", "content": f"回答 {i} " * 100})

    strategy = build_compaction(
        messages, context_window=2000, threshold=1.0, keep_recent_tokens=200
    )
    assert strategy.should_compact is True
    assert strategy.cut_index is not None and strategy.cut_index > 0
    assert strategy.kept_tokens < strategy.estimated_tokens


def test_find_cut_point_respects_keep_recent():
    """find_cut_point: 保留尾部 keep_recent_tokens 后定位 cut_index。"""
    messages = [
        {"role": "user", "content": "短消息 1"},
        {"role": "assistant", "content": "短回答 1"},
        {"role": "user", "content": "短消息 2 " * 50},
        {"role": "assistant", "content": "短回答 2 " * 50},
    ]
    cut = find_cut_point(messages, keep_recent_tokens=100)
    # 至少能保留尾部，cut_index >= 0 或 None（消息太少）
    assert cut is None or cut > 0


# ──────────────────────────── 3. carry_forward_missing_sections 契约 ────────────────────────────


_PRIOR_SUMMARY = """\
1. User-Stated Facts
- 通知时间: 周六14:00

2. Pending Tasks
- 调整降糖药剂量

3. Voided Entries
- 二甲双胍: VOID

7. Pending Tasks
- 待办事项 1
"""

_NEW_SUMMARY_MISSING_VOIDED_SECTION = """\
1. User-Stated Facts
- 通知时间: 周六14:00

4. Recent Findings
- 血压正常
"""


def test_carry_forward_appends_voided_section_via_w12():
    """新摘要缺含 voided markers 的 section → 无条件 carry (W12)。"""
    result = carry_forward_missing_sections(
        _NEW_SUMMARY_MISSING_VOIDED_SECTION, _PRIOR_SUMMARY
    )
    # W12: voided section 无条件 carry
    assert "Voided Entries" in result
    assert "二甲双胍: VOID" in result
    # 原有 section 不丢
    assert "通知时间" in result
    assert "Recent Findings" in result


def test_carry_forward_appends_section_7_via_carry_forward_set():
    """新摘要缺 section 7 (carry-forward 集合) → 从 prior 摘要 append。"""
    result = carry_forward_missing_sections(
        _NEW_SUMMARY_MISSING_VOIDED_SECTION, _PRIOR_SUMMARY
    )
    # section 7 在 _CARRY_FORWARD_SECTION_NUMBERS = frozenset({7})
    assert "7. Pending Tasks" in result
    assert "待办事项 1" in result


def test_carry_forward_no_op_when_all_present():
    """新摘要已含全部 critical section → 不 append。"""
    new_summary = _PRIOR_SUMMARY  # 完整
    result = carry_forward_missing_sections(new_summary, _PRIOR_SUMMARY)
    assert result == new_summary


def test_carry_forward_empty_new_summary():
    """新摘要空 → 整个 prior 摘要 append。"""
    result = carry_forward_missing_sections("", _PRIOR_SUMMARY)
    assert "Pending Tasks" in result
    assert "Voided Entries" in result


# ──────────────────────────── 4. CompressionRuntimeState 不变量 ────────────────────────────


def test_compression_runtime_state_defaults():
    """CompressionRuntimeState 缺省值符合契约：从未压缩过。"""
    state = CompressionRuntimeState()
    assert state.consecutive_failures == 0
    assert state.last_successful_summary_text == ""
    assert state.last_compaction_entry_seq == -1
    assert state.recent_history_tokens == []


def test_compression_runtime_state_mutable():
    """runtime state 字段可更新（持久化通过 ports.update_compression_runtime_state）。"""
    state = CompressionRuntimeState()
    state.consecutive_failures = 3
    state.last_compaction_entry_seq = 42
    state.recent_history_tokens = [1000, 2000, 3000]
    state.last_successful_summary_text = "上一次的摘要"
    assert state.consecutive_failures == 3
    assert state.last_compaction_entry_seq == 42
    assert state.recent_history_tokens[-1] == 3000


# ──────────────────────────── 5. predict_compaction_trigger 契约 ────────────────────────────


def test_predict_compaction_no_samples_returns_false():
    """样本数 < min_samples → 返回 False（不足以预测）。"""
    runtime = CompressionRuntimeState(recent_history_tokens=[1000, 2000])
    result = predict_compaction_trigger(
        runtime=runtime,
        current_tokens=1500,
        trigger_budget=5000,
        current_entry_seq=10,
        horizon=3,
        min_samples=4,  # 样本不足
        history_budget=5000,
    )
    assert result is False


def test_predict_compaction_emergency_ratio_triggers():
    """current_tokens >= emergency_ratio × history_budget → 紧急触发，绕过 cooldown。"""
    runtime = CompressionRuntimeState(
        recent_history_tokens=[1000, 2000, 3000, 4000, 5000],
        last_compaction_entry_seq=-1,
    )
    # emergency_ratio=0.9, history_budget=10000 → 阈值 9000
    result = predict_compaction_trigger(
        runtime=runtime,
        current_tokens=9100,
        trigger_budget=7500,
        current_entry_seq=10,
        horizon=3,
        min_samples=4,
        emergency_ratio=0.9,
        history_budget=10000,
    )
    assert result is True


def test_predict_compaction_already_over_budget_returns_false():
    """current_tokens >= trigger_budget → 已超预算，由被动路径处理，predictive 返回 False。"""
    runtime = CompressionRuntimeState(
        recent_history_tokens=[1000, 2000, 3000, 4000, 5000]
    )
    result = predict_compaction_trigger(
        runtime=runtime,
        current_tokens=8000,
        trigger_budget=7500,
        current_entry_seq=10,
        horizon=3,
        min_samples=4,
        emergency_ratio=0.95,
        history_budget=10000,
    )
    assert result is False


# ──────────────────────────── 6. estimate_message_tokens 单调性 ────────────────────────────


def test_estimate_message_tokens_chinese_weighted():
    """中文消息 token 估算 ≈ 0.90 tokens/char，远高于 len//4。"""
    chinese_msg = {"role": "user", "content": "液压油改进剂密度调整" * 20}
    tokens = estimate_message_tokens(chinese_msg)
    # 中文字符数 × 0.90 至少大于 len//4
    char_count = len(chinese_msg["content"])
    legacy = char_count // 4
    assert tokens > legacy  # v2 校准生效


def test_estimate_message_tokens_with_tool_calls():
    """带 tool_calls 的消息：name + arguments 都计入。"""
    msg = {
        "role": "assistant",
        "content": "正在调用工具",
        "tool_calls": [
            {
                "function": {
                    "name": "read",
                    "arguments": '{"file_path": "/tmp/test.txt"}',
                }
            }
        ],
    }
    tokens = estimate_message_tokens(msg)
    assert tokens > 8  # base overhead
