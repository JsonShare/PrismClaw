"""预算上限禁用回归（三层防护架构）。

* ``BudgetGuard`` 的轮次上限为 0 时任何迭代都不判超限（无 >0 守卫时
  ``iteration >= 0`` 恒真，首轮即误判）；为正数时仍在 N 处判超限
  （能力保留，scheduler cron 配额依赖此路径）。
* run_seconds=0 → run_deadline_s=0 → ``remaining_run_seconds`` 返回
  None，所有依赖该函数的检查点原生跳过。
* agent.yaml 实际配置为 0（防配置回退）。
"""
from __future__ import annotations

import time
from pathlib import Path

from prismclaw.engine.execution.control.kernel import Budget, BudgetGuard
from prismclaw.engine.foundation.runtime_util import remaining_run_seconds
from prismclaw.infra.config_io import load_document

_AGENT_CONFIG = Path(__file__).resolve().parents[1] / "configs" / "agent.yaml"


def _guard(max_iterations: int) -> BudgetGuard:
    return BudgetGuard(Budget(
        max_iterations=max_iterations,
        run_deadline_s=0.0,
        run_started=0.0,
    ))


def test_budget_zero_max_iterations_never_fires():
    guard = _guard(0)
    for it in (0, 1, 150, 9999):
        assert guard.check(iteration=it) is None, (
            f"max_iterations=0 wrongly fired at iteration {it}"
        )


def test_budget_positive_max_iterations_still_fires():
    guard = _guard(5)
    assert guard.check(iteration=4) is None
    assert guard.check(iteration=5) == "budget_iterations"


def test_remaining_run_seconds_zero_disables():
    assert remaining_run_seconds(0.0, 0.0) is None
    # 正 deadline 仍返回剩余值（能力保留）
    started = time.monotonic()
    assert remaining_run_seconds(3600.0, started) > 3500


def test_agent_config_effort_tiers_present():
    """预算上限已由 effort 档位取代 max_iterations/run_seconds。"""
    cfg = load_document(_AGENT_CONFIG)
    agent = cfg["agent"]
    assert "max_iterations" not in agent, "max_iterations 已由 effort 档位取代"
    assert "run_seconds" not in agent["timeouts"], "run_seconds 已由 effort.hard_s 取代"
    effort = agent["effort"]
    for tier in ("quick", "standard", "deep"):
        assert effort[tier]["steps"] > 0
        assert effort[tier]["tool_calls"] > 0
        assert effort[tier]["hard_s"] > 0
        assert effort[tier]["soft_s"] <= effort[tier]["hard_s"]


