"""doom-loop 防护链路回归测试。

锁定 P1/P2 重构后的契约：
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


def _cfg(warn_at: int = 3, cap: int = 3) -> RunLoopTuningConfig:
    cfg = RunLoopTuningConfig()
    cfg.repeated_tool_signature_threshold = warn_at
    cfg.doom_loop_threshold = cap
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
    # cap=8 → 2*8=16 > 12-entry window → clamped
    escalate_above, absolute_max = doom_loop_thresholds(_cfg(warn_at=3, cap=8))
    assert (escalate_above, absolute_max) == (8, 12)


# ── record：唯一写入点 + 配对 token ──


def test_record_writes_paired_token_once():
    ts = ToolLoopState()
    _observe(ts, "web_fetch:abc", "fp1")
    assert ts.recent_tool_signatures == ["web_fetch:abc@fp1"]
    assert ts.per_tool_call_counts == {"web_fetch": 1}
    assert ts.consecutive_errors == 0


def test_record_window_trims_to_limit():
    ts = ToolLoopState()
    for i in range(15):
        _observe(ts, f"web_fetch:{i}", f"fp{i}")
    assert len(ts.recent_tool_signatures) == 12


def test_record_error_counters():
    ts = ToolLoopState()
    _observe(ts, "t:a", "f1", is_error=True)
    _observe(ts, "t:a", "f2", is_error=True)
    assert ts.consecutive_errors == 2
    _observe(ts, "t:a", "f3", is_error=False)
    assert ts.consecutive_errors == 0


# ── 阶梯：warn → 纠正 → freeze（不再直接 terminate）──


def test_ladder_soft_warn_at_warn_at():
    ts = ToolLoopState()
    for _ in range(3):
        _observe(ts, "web_fetch:s", "fp")
    out = evaluate_tool_loop_policies(state=ts, tool_name="web_fetch", config=_cfg())
    assert out.action == "warn"
    assert "已重复 3 次" in out.guidance


def test_ladder_correction_above_escalate_above():
    ts = ToolLoopState()
    for _ in range(5):
        _observe(ts, "web_fetch:s", "fp")
    out = evaluate_tool_loop_policies(state=ts, tool_name="web_fetch", config=_cfg())
    assert out.action == "warn"
    assert "doom-loop" in out.guidance


def test_ladder_freeze_at_absolute_max():
    ts = ToolLoopState()
    for _ in range(6):
        _observe(ts, "web_fetch:s", "fp")
    out = evaluate_tool_loop_policies(state=ts, tool_name="web_fetch", config=_cfg())
    assert out.action == "freeze"
    assert out.frozen_signature == "web_fetch:s"
    assert "冻结" in out.guidance


def test_ladder_never_terminates_from_streak():
    ts = ToolLoopState()
    for _ in range(12):  # saturate the whole window
        _observe(ts, "web_fetch:s", "fp")
    out = evaluate_tool_loop_policies(state=ts, tool_name="web_fetch", config=_cfg())
    assert out.action == "freeze"


# ── 结果变化 → streak 归 1（配对 token 语义）──


def test_result_change_resets_streak():
    ts = ToolLoopState()
    for _ in range(5):
        _observe(ts, "web_fetch:s", "fp-same")
    _observe(ts, "web_fetch:s", "fp-CHANGED")  # same call, new result
    out = evaluate_tool_loop_policies(state=ts, tool_name="web_fetch", config=_cfg())
    assert out.action == "proceed"


def test_different_signature_resets_streak():
    ts = ToolLoopState()
    _observe(ts, "t:a", "f1")
    _observe(ts, "t:a", "f2")
    _observe(ts, "t:b", "f3")
    out = evaluate_tool_loop_policies(state=ts, tool_name="web_fetch", config=_cfg())
    assert out.action == "proceed"


# ── evaluate 只读 ──


def test_evaluate_is_read_only():
    ts = ToolLoopState()
    for _ in range(6):
        _observe(ts, "web_fetch:s", "fp")
    ts.frozen_tools.add("stale")
    snapshot_sigs = list(ts.recent_tool_signatures)
    snapshot_counts = dict(ts.per_tool_call_counts)
    snapshot_frozen_tools = set(ts.frozen_tools)
    snapshot_errors = ts.consecutive_errors
    evaluate_tool_loop_policies(state=ts, tool_name="web_fetch", config=_cfg())
    assert ts.recent_tool_signatures == snapshot_sigs
    assert ts.per_tool_call_counts == snapshot_counts
    assert ts.frozen_tools == snapshot_frozen_tools
    assert ts.consecutive_errors == snapshot_errors


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
    # v2.2：frozen_call_defied 不再驱动终止（路径 D 已移除），转为智能纠偏层
    # 的偏离诊断输入——只纠偏不掐断，终止仅归 CompletionKernel 硬边界
    # （BudgetGuard 预算码 / blocked_declared / blocked_no_progress）。
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
    restored = ToolLoopState.from_dict({"consecutive_errors": 1})
    assert restored.frozen_call_signatures == {}


# ── 有界循环检测（Check 2b，eadc0d 类）──
#
# 场景：结果指纹一直在变（fp 各不相同），尾随 streak 恒为 1，
# Check 2 的逐字节相同阶梯永远够不到；但 12 条观察窗口内始终只有
# 2 个独特组合——agent 被困在小集合里、没有产生任何新信息。


def _observe_cycling(ts: ToolLoopState, rounds: int) -> None:
    """在两种「调用+结果」组合之间反复交替（非连续重复）。

    注意结果指纹也是固定的：有界循环的判据是窗口内独特
    "{sig}@{fp}" 组合数 <= 2——结果指纹若每次都变，说明 agent 在
    消费新信息，按进度驱动语义不算卡死（尾随连续重复由 Check 2 兜底）。
    """
    for i in range(rounds):
        if i % 2 == 0:
            _observe(ts, "web_fetch:a", "fp-a")
        else:
            _observe(ts, "web_fetch:b", "fp-b")


def test_cycling_streak_counts_and_resets():
    ts = ToolLoopState()
    _observe_cycling(ts, 12)  # 窗口恰好填满
    assert ts.cycling_loop_streak == 1
    # 出现任何新组合 → 归 0（进度信号，不是迭代计数）
    _observe(ts, "web_fetch:c", "fp-c")
    assert ts.cycling_loop_streak == 0


def test_cycling_window_not_full_never_counts():
    ts = ToolLoopState()
    _observe_cycling(ts, 11)  # 差 1 条不满窗
    assert ts.cycling_loop_streak == 0
    out = evaluate_tool_loop_policies(state=ts, tool_name="web_fetch", config=_cfg())
    assert out.action == "proceed"


def test_cycling_warns_at_warn_at():
    ts = ToolLoopState()
    _observe_cycling(ts, 15)  # 12 填窗 + 3 次 cycling 计数（== warn_at）
    out = evaluate_tool_loop_policies(state=ts, tool_name="web_fetch", config=_cfg())
    assert out.action == "warn"
    assert "有界循环" in out.guidance


def test_cycling_suspicion_at_absolute_max():
    # v2.2：达 absolute_max 不再由本层 terminate（路径 D 已移除），只产 warn
    # 引导。cycling_loop_streak 转为智能纠偏层的偏离诊断输入——只纠偏不
    # 掐断，终止仅归 CompletionKernel 硬边界（BudgetGuard 预算码 /
    # blocked_declared / blocked_no_progress）。
    ts = ToolLoopState()
    _observe_cycling(ts, 18)  # 12 填窗 + 6 次 cycling 计数（== absolute_max）
    out = evaluate_tool_loop_policies(state=ts, tool_name="web_fetch", config=_cfg())
    assert out.action == "warn"
    assert "有界循环" in out.guidance
    # 嫌疑信号在 state 上（判定函数输入源）
    _escalate, _max = doom_loop_thresholds(_cfg())
    assert ts.cycling_loop_streak >= _max


def test_cycling_new_progress_prevents_termination():
    ts = ToolLoopState()
    _observe_cycling(ts, 17)  # 差 1 次到达 absolute_max
    _observe(ts, "web_fetch:c", "fp-c")  # 真正的新组合 → streak 归 0
    out = evaluate_tool_loop_policies(state=ts, tool_name="web_fetch", config=_cfg())
    assert out.action == "proceed"


def test_identical_result_ladder_wins_over_cycling_freeze():
    # 12 条完全相同的观察：unique=1 也满足 cycling 条件（窗口恰好在
    # 第 12 条填满 → streak=1），但 Check 2 的逐字节相同连续重复阶梯
    # 先判定 freeze（给模型自救机会），不得终止。
    ts = ToolLoopState()
    for _ in range(12):
        _observe(ts, "web_fetch:s", "fp")
    assert ts.cycling_loop_streak == 1
    out = evaluate_tool_loop_policies(state=ts, tool_name="web_fetch", config=_cfg())
    assert out.action == "freeze"


def test_cycling_streak_checkpoint_roundtrip():
    ts = ToolLoopState()
    _observe_cycling(ts, 14)
    restored = ToolLoopState.from_dict(ts.to_dict())
    assert restored.cycling_loop_streak == ts.cycling_loop_streak


# ── Check 1 / Check 3：计数限制降级为 warn（不终止 run）──


def test_tool_max_uses_downgrades_to_warn():
    cfg = _cfg()
    cfg.tool_max_uses = {"web_fetch": 2}
    ts = ToolLoopState()
    _observe(ts, "web_fetch:s", "fp1")
    _observe(ts, "web_fetch:s", "fp2")
    out = evaluate_tool_loop_policies(state=ts, tool_name="web_fetch", config=cfg)
    assert out.action == "warn"
    assert out.guidance is not None


def test_error_fatigue_downgrades_to_warn():
    cfg = _cfg()
    ts = ToolLoopState()
    for i in range(3):
        _observe(ts, f"web_fetch:v{i}", f"fp-err{i}", is_error=True)
    assert ts.consecutive_errors == 3
    out = evaluate_tool_loop_policies(state=ts, tool_name="web_fetch", config=cfg)
    assert out.action == "warn"
    assert out.guidance is not None