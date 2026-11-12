"""doom-loop 签名冻结回归测试。

锁定重构后的契约：
  - record_tool_call_observation 是唯一写入点（每回合恰好一次）
  - 配对 token "{sig}@{fp}"：结果一变 streak 自动归 1
  - 阶梯：soft warn → 结构化纠正 → freeze（冻结调用而非终止任务）
  - 冻结升级：反复重试被冻结的调用达 escalate_above 置 frozen_call_defied
    嫌疑标志，run 级终止由 CompletionKernel 统一裁决
  - checkpoint 序列化往返保持 frozen_call_signatures
"""
from __future__ import annotations

from prismclaw.engine.execution.dispatch.call_handling import (
    check_frozen_call,
    doom_loop_thresholds,
    evaluate_tool_loop_policies,
    record_tool_call_observation,
)
from prismclaw.engine.state.tool_loop_state import ToolLoopState
from prismclaw.schema.config import RunLoopTuningConfig


def _cfg(warn_at: int = 3) -> RunLoopTuningConfig:
    cfg = RunLoopTuningConfig()
    cfg.stuck.signature_warn = warn_at
    return cfg


def _observe(ts: ToolLoopState, sig: str, fp: str, *, is_error: bool = False) -> None:
    record_tool_call_observation(
        ts,
        tool_name="web_fetch",
        tool_signature=sig,
        result_fingerprint=fp,
        is_error=is_error,
    )


# ── 阈梯阈值推导 ──


def test_thresholds_default():
    escalate_above, absolute_max = doom_loop_thresholds(_cfg())
    assert (escalate_above, absolute_max) == (3, 6)


def test_thresholds_clamped_to_window():
    # warn_at=8 → 2*8=16 > 12-entry window → clamped
    escalate_above, absolute_max = doom_loop_thresholds(_cfg(warn_at=8))
    assert (escalate_above, absolute_max) == (8, 12)


# ── record：唯一写入点 + 配对 token ──


def test_record_writes_paired_token_once():
    ts = ToolLoopState()
    _observe(ts, "web_fetch:abc", "fp1")
    assert ts.recent_tool_signatures == ["web_fetch:abc@fp1"]


def test_record_window_trims_to_limit():
    ts = ToolLoopState()
    for i in range(15):
        _observe(ts, f"web_fetch:{i}", f"fp{i}")
    assert len(ts.recent_tool_signatures) == 12


# ── 阶梯：warn → 纠正 → freeze（不再直接 terminate）──


def test_ladder_soft_warn_at_warn_at():
    ts = ToolLoopState()
    for _ in range(3):
        _observe(ts, "web_fetch:s", "fp")
    out = evaluate_tool_loop_policies(state=ts, config=_cfg())
    assert out.action == "warn"
    assert "已重复 3 次" in out.guidance


def test_ladder_correction_above_escalate_above():
    ts = ToolLoopState()
    for _ in range(5):
        _observe(ts, "web_fetch:s", "fp")
    out = evaluate_tool_loop_policies(state=ts, config=_cfg())
    assert out.action == "warn"
    assert "doom-loop" in out.guidance


def test_ladder_freeze_at_absolute_max():
    ts = ToolLoopState()
    for _ in range(6):
        _observe(ts, "web_fetch:s", "fp")
    out = evaluate_tool_loop_policies(state=ts, config=_cfg())
    assert out.action == "freeze"
    assert out.frozen_signature == "web_fetch:s"
    assert "冻结" in out.guidance


def test_ladder_never_terminates_from_streak():
    ts = ToolLoopState()
    for _ in range(12):  # saturate the whole window
        _observe(ts, "web_fetch:s", "fp")
    out = evaluate_tool_loop_policies(state=ts, config=_cfg())
    assert out.action == "freeze"


# ── 结果变化 → streak 归 1（配对 token 语义）──


def test_result_change_resets_streak():
    ts = ToolLoopState()
    for _ in range(5):
        _observe(ts, "web_fetch:s", "fp-same")
    _observe(ts, "web_fetch:s", "fp-CHANGED")  # same call, new result
    out = evaluate_tool_loop_policies(state=ts, config=_cfg())
    assert out.action == "proceed"


def test_different_signature_resets_streak():
    ts = ToolLoopState()
    _observe(ts, "t:a", "f1")
    _observe(ts, "t:a", "f2")
    _observe(ts, "t:b", "f3")
    out = evaluate_tool_loop_policies(state=ts, config=_cfg())
    assert out.action == "proceed"


# ── evaluate 只读 ──


def test_evaluate_is_read_only():
    ts = ToolLoopState()
    for _ in range(6):
        _observe(ts, "web_fetch:s", "fp")
    ts.frozen_tools.add("stale")
    snapshot_sigs = list(ts.recent_tool_signatures)
    snapshot_frozen_tools = set(ts.frozen_tools)
    evaluate_tool_loop_policies(state=ts, config=_cfg())
    assert ts.recent_tool_signatures == snapshot_sigs
    assert ts.frozen_tools == snapshot_frozen_tools


# ── 冻结检查：block → 升级 frozen_call_defied 嫌疑（终止权上交）──


def test_check_frozen_call_unfrozen_proceeds():
    ts = ToolLoopState()
    out = check_frozen_call(
        state=ts, tool_signature="web_fetch:other", config=_cfg(),
    )
    assert out.action == "proceed"


def test_check_frozen_call_blocks_and_escalates():
    ts = ToolLoopState()
    sig = "web_fetch:s"
    # Check 2 判定 freeze 后注册（初始 0）
    ts.frozen_call_signatures[sig] = 0

    # 第 1 次重试 → block（计数 1）
    out = check_frozen_call(state=ts, tool_signature=sig, config=_cfg())
    assert out.action == "freeze"
    assert ts.frozen_call_signatures[sig] == 1
    assert "冻结" in out.guidance

    # 第 2 次重试 → block（计数 2）
    out = check_frozen_call(state=ts, tool_signature=sig, config=_cfg())
    assert out.action == "freeze"
    assert ts.frozen_call_signatures[sig] == 2

    # 第 3 次重试（== escalate_above）→ 置 frozen_call_defied 标志，仍 block。
    out = check_frozen_call(state=ts, tool_signature=sig, config=_cfg())
    assert out.action == "freeze"
    assert ts.frozen_call_defied is True
    assert "冻结" in out.guidance


def test_check_frozen_call_counts_once_per_turn():
    """同一签名一回合只计一次数：并行 N 个相同调用不耗 N 次配额。"""
    ts = ToolLoopState()
    sig = "web_fetch:s"
    ts.frozen_call_signatures[sig] = 0

    counted: set[str] = set()  # 模拟 run_loop 一回合内传递的集合
    outs = [
        check_frozen_call(state=ts, tool_signature=sig, config=_cfg(),
                          counted_this_turn=counted)
        for _ in range(5)  # 模型一回合并行发 5 个相同调用
    ]
    assert all(o.action == "freeze" for o in outs)
    # 5 次命中只计 1 次数
    assert ts.frozen_call_signatures[sig] == 1

    # 下一回合（新集合）再命中 → 计数 2
    out = check_frozen_call(state=ts, tool_signature=sig, config=_cfg(),
                            counted_this_turn=set())
    assert out.action == "freeze"
    assert ts.frozen_call_signatures[sig] == 2


# ── checkpoint 序列化往返 ──


def test_checkpoint_roundtrip_keeps_frozen_signatures():
    ts = ToolLoopState()
    ts.frozen_call_signatures["web_fetch:s"] = 2
    ts.recent_tool_signatures = ["web_fetch:s@fp"] * 3
    restored = ToolLoopState.from_dict(ts.to_dict())
    assert restored.frozen_call_signatures == {"web_fetch:s": 2}
    assert restored.recent_tool_signatures == ["web_fetch:s@fp"] * 3


def test_from_dict_without_frozen_field():
    # 旧 checkpoint（无 frozen_call_signatures 键）可恢复
    restored = ToolLoopState.from_dict({"recent_tool_signatures": []})
    assert restored.frozen_call_signatures == {}
