"""CompletionKernel / BudgetGuard / RecoveryPolicy 决策表。

只断言新路径：预算 → 恢复 → 契约 → 准则 → 修复额度。
准则评估用真实 ``ArtifactScanner``（临时目录），不桩化文件系统。
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from prismclaw.engine.execution.control.artifacts import ArtifactScanner
from prismclaw.engine.execution.control.completion import (
    CompletionContract,
    CompletionReviewer,
    Criterion,
)
from prismclaw.engine.execution.control.kernel import (
    Budget,
    BudgetGuard,
    CompletionKernel,
    CompletionState,
    Decision,
    LoopEvent,
    RecoveryPolicy,
)
from prismclaw.schema.agent.task_runtime import EvidenceRecord


def _kernel(
    root: Path,
    *,
    contract: CompletionContract | None,
    max_iterations: int = 0,
    max_usd: float | None = None,
    limit: int = 3,
    chain: tuple[str, ...] = (),
) -> CompletionKernel:
    return CompletionKernel(
        state=CompletionState(contract=contract),
        reviewer=CompletionReviewer(ArtifactScanner(root)),
        budget=BudgetGuard(Budget(
            max_iterations=max_iterations,
            run_deadline_s=0.0,
            run_started=0.0,
            max_usd=max_usd,
        )),
        recovery=RecoveryPolicy(limit=limit, chain=chain),
    )


def _write(root: Path, rel: str, text: str = "x") -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _evidence(path: str) -> list[EvidenceRecord]:
    return [
        EvidenceRecord(
            evidence_id="e1",
            task_id="t1",
            tool_name="write_file",
            status="ok",
            wrote_paths=(path,),
            origin="main",
        ),
    ]


# ---------------------------------------------------------------------------
# 预算：唯一先于一切的硬边界
# ---------------------------------------------------------------------------

def test_budget_iterations_terminates_before_completion(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path, contract=None, max_iterations=5)
    decision = kernel.decide(LoopEvent(site="preflight", iteration=5))
    assert (decision.verdict, decision.code) == ("TERMINATE", "budget_iterations")


def test_budget_warning_threshold_not_terminating() -> None:
    guard = BudgetGuard(Budget(
        max_iterations=5, run_deadline_s=0.0, run_started=0.0, max_usd=None,
    ))
    assert guard.warn(iteration=4, usd_cost=0.0) is None
    assert guard.warn(iteration=5, usd_cost=0.0) == "budget_iterations"


def test_budget_usd_terminates(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path, contract=None, max_usd=1.0)
    decision = kernel.decide(LoopEvent(site="post", iteration=0), usd_cost=1.5)
    assert (decision.verdict, decision.code) == ("TERMINATE", "budget_usd")


def test_budget_zero_iterations_never_fires(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path, contract=None, max_iterations=0)
    for iteration in (0, 10, 999):
        assert kernel.decide(LoopEvent(site="post", iteration=iteration)).verdict == "PROCEED"


# ---------------------------------------------------------------------------
# 恢复策略：可自愈故障产 RESTART，不可自愈故障只给一次重规划
# ---------------------------------------------------------------------------

def test_all_blocked_restarts_once_then_terminates(tmp_path: Path) -> None:
    """全拒不可自愈：第一次给一次重规划，第二次直接终态（旧实现空转至 limit）。"""
    kernel = _kernel(tmp_path, contract=None, limit=3)
    first = kernel.decide(LoopEvent(site="all_blocked", iteration=0))
    assert (first.verdict, first.code) == ("RESTART", "all_tools_denied")
    assert [gap.code for gap in first.gaps] == ["all_tools_denied_retry"]

    second = kernel.decide(LoopEvent(site="all_blocked", iteration=1))
    assert second.verdict == "TERMINATE"
    assert second.code == "blocked_tools_denied"
    assert [gap.code for gap in second.gaps] == ["all_tools_denied"]

    # 终态稳定：额度已用尽，后续全拒不得再回到 RESTART
    assert kernel.decide(
        LoopEvent(site="all_blocked", iteration=2)
    ).verdict == "TERMINATE"


def test_recovery_chain_cleared_only_by_executed_tool_turn(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path, contract=None, limit=3)
    kernel.decide(LoopEvent(site="all_blocked", iteration=0))  # 第一次 RESTART
    # preflight（未执行工具）不再清零，否则全拒熔断永远不触发
    kernel.decide(LoopEvent(site="preflight", iteration=1))
    assert kernel.decide(
        LoopEvent(site="all_blocked", iteration=1)
    ).verdict == "TERMINATE"
    # 真正执行过工具的回合才清零：新的全拒重新获得一次重规划机会
    restarted = _kernel(
        tmp_path, contract=None, limit=3,
        chain=("all_tools_denied",),
    )
    restarted.decide(LoopEvent(site="post", iteration=0))
    assert restarted.decide(
        LoopEvent(site="all_blocked", iteration=1)
    ).verdict == "RESTART"


def test_self_healing_failures_share_global_limit() -> None:
    policy = RecoveryPolicy(limit=3)
    assert [policy.consult("compression_restart") for _ in range(3)] == [
        "compression_restart",
    ] * 3
    assert policy.consult("compression_restart") is None


def test_persisted_non_self_healing_chain_consumes_quota() -> None:
    """断点续跑：历史链里的全拒必须继续占用额度，不能被重置成满额。"""
    policy = RecoveryPolicy(limit=3, chain=("all_tools_denied",))
    assert policy.consult("all_tools_denied") is None


def test_note_progress_resets_quota() -> None:
    policy = RecoveryPolicy(limit=3, chain=("all_tools_denied",))
    policy.note_progress()
    assert policy.consult("all_tools_denied") == "all_tools_denied"


def test_per_code_limit_overrides_class_default() -> None:
    policy = RecoveryPolicy(limit=3, per_code_limit={"all_tools_denied": 2})
    assert policy.consult("all_tools_denied") == "all_tools_denied"
    assert policy.consult("all_tools_denied") == "all_tools_denied"
    assert policy.consult("all_tools_denied") is None


def test_persisted_chain_seeds_policy() -> None:
    policy = RecoveryPolicy(limit=3, chain=("compression_restart", "all_tools_denied"))
    assert policy.chain == ["compression_restart", "all_tools_denied"]


def test_verdict_metric_labels_use_wire_code(tmp_path: Path) -> None:
    """``TerminalReason`` 是 str 枚举：指标标签不得带 ``TerminalReason.`` 前缀。"""
    from prismclaw.engine.foundation.metrics import snapshot

    kernel = _kernel(tmp_path, contract=None, max_iterations=1)
    kernel.decide(LoopEvent(site="preflight", iteration=1))

    keys = [
        key for key in snapshot()["counters"]
        if key.startswith("completion.verdict_total")
    ]
    assert any("code=budget_iterations" in key for key in keys), keys
    assert all("TerminalReason." not in key for key in keys), keys


# ---------------------------------------------------------------------------
# 完成契约
# ---------------------------------------------------------------------------

def test_no_contract_completes_on_first_stop(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path, contract=None)
    decision = kernel.decide(LoopEvent(site="preflight", iteration=0, is_stop=True))
    assert (decision.verdict, decision.code) == ("COMPLETE", "complete_open")


def test_strict_contract_gap_repairs_once_then_terminates(tmp_path: Path) -> None:
    contract = CompletionContract("strict", (Criterion("file_exists", "outputs/a.md"),))
    kernel = _kernel(tmp_path, contract=contract)
    first = kernel.decide(LoopEvent(site="preflight", iteration=0, is_stop=True))
    assert (first.verdict, first.code) == ("REPAIR", "repair")
    assert [gap.code for gap in first.gaps] == ["file_missing"]
    second = kernel.decide(LoopEvent(site="preflight", iteration=1, is_stop=True))
    assert (second.verdict, second.code) == ("TERMINATE", "blocked_no_progress")


def test_strict_contract_accepted_completes_verified(tmp_path: Path) -> None:
    _write(tmp_path, "outputs/a.md")
    contract = CompletionContract("strict", (Criterion("file_exists", "outputs/a.md"),))
    kernel = _kernel(tmp_path, contract=contract)
    kernel.state.ledger = _evidence("outputs/a.md")
    decision = kernel.decide(LoopEvent(site="preflight", iteration=0, is_stop=True))
    assert (decision.verdict, decision.code) == ("COMPLETE", "complete_verified")


def test_empty_file_is_unmet_not_accepted(tmp_path: Path) -> None:
    _write(tmp_path, "outputs/a.md", "")
    contract = CompletionContract("strict", (Criterion("file_nonempty", "outputs/a.md"),))
    kernel = _kernel(tmp_path, contract=contract)
    decision = kernel.decide(LoopEvent(site="preflight", iteration=0, is_stop=True))
    assert (decision.verdict, decision.code) == ("REPAIR", "repair")
    assert [gap.code for gap in decision.gaps] == ["file_empty"]


def test_advisory_nudges_once_then_completes(tmp_path: Path) -> None:
    contract = CompletionContract("advisory", (Criterion("file_exists"),))
    kernel = _kernel(tmp_path, contract=contract)
    first = kernel.decide(LoopEvent(site="preflight", iteration=0, is_stop=True))
    assert (first.verdict, first.code) == ("REPAIR", "advisory_nudge")
    second = kernel.decide(LoopEvent(site="preflight", iteration=1, is_stop=True))
    assert (second.verdict, second.code) == ("COMPLETE", "complete_advisory")


def test_unverifiable_does_not_block_completion(tmp_path: Path) -> None:
    contract = CompletionContract("strict", (Criterion("unverifiable", "custom.bin"),))
    kernel = _kernel(tmp_path, contract=contract)
    decision = kernel.decide(LoopEvent(site="preflight", iteration=0, is_stop=True))
    assert (decision.verdict, decision.code) == ("COMPLETE", "complete_verified")


# ---------------------------------------------------------------------------
# 阻塞申报
# ---------------------------------------------------------------------------

def test_declares_blocked_without_attempt_is_rejected_once(tmp_path: Path) -> None:
    contract = CompletionContract("strict", (Criterion("file_exists", "outputs/a.md"),))
    kernel = _kernel(tmp_path, contract=contract)
    first = kernel.decide(LoopEvent(
        site="preflight", iteration=0, is_stop=True, declares_blocked=True,
    ))
    assert (first.verdict, first.code) == ("REPAIR", "blocked_rejected")
    assert kernel.state.repairs == 1  # 拒绝也计数，防止反复申报
    second = kernel.decide(LoopEvent(
        site="preflight", iteration=1, is_stop=True, declares_blocked=True,
    ))
    assert (second.verdict, second.code) == ("TERMINATE", "blocked_declared")


def test_declares_blocked_with_attempt_is_accepted_immediately(tmp_path: Path) -> None:
    contract = CompletionContract("strict", (Criterion("file_exists", "outputs/a.md"),))
    kernel = _kernel(tmp_path, contract=contract)
    kernel.state.ledger = _evidence("outputs/a.md")
    decision = kernel.decide(LoopEvent(
        site="preflight", iteration=0, is_stop=True, declares_blocked=True,
    ))
    assert (decision.verdict, decision.code) == ("TERMINATE", "blocked_declared")


def test_declares_blocked_but_review_accepted_completes(tmp_path: Path) -> None:
    _write(tmp_path, "outputs/a.md")
    contract = CompletionContract("strict", (Criterion("file_exists", "outputs/a.md"),))
    kernel = _kernel(tmp_path, contract=contract)
    kernel.state.ledger = _evidence("outputs/a.md")
    decision = kernel.decide(LoopEvent(
        site="preflight", iteration=0, is_stop=True, declares_blocked=True,
    ))
    assert (decision.verdict, decision.code) == ("COMPLETE", "complete_verified")


# ---------------------------------------------------------------------------
# Decision / LoopEvent 闭合
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("verdict", "expected"),
    [
        ("PROCEED", True),
        ("RESTART", True),
        ("REPAIR", True),
        ("COMPLETE", False),
        ("TERMINATE", False),
    ],
)
def test_should_continue_closure(verdict: str, expected: bool) -> None:
    assert Decision(verdict, "x").should_continue is expected


def test_loop_event_is_frozen_and_replaceable() -> None:
    event = LoopEvent(site="preflight", iteration=3)
    stop = replace(event, is_stop=True, declares_blocked=True)
    assert (stop.site, stop.iteration, stop.is_stop, stop.declares_blocked) == (
        "preflight", 3, True, True,
    )
