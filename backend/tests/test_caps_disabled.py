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
        max_usd=None,
    ))


def test_budget_zero_max_iterations_never_fires():
    guard = _guard(0)
    for it in (0, 1, 150, 9999):
        assert guard.check(iteration=it, usd_cost=0.0) is None, (
            f"max_iterations=0 wrongly fired at iteration {it}"
        )


def test_budget_positive_max_iterations_still_fires():
    guard = _guard(5)
    assert guard.check(iteration=4, usd_cost=0.0) is None
    assert guard.check(iteration=5, usd_cost=0.0) == "budget_iterations"


def test_remaining_run_seconds_zero_disables():
    assert remaining_run_seconds(0.0, 0.0) is None
    # 正 deadline 仍返回剩余值（能力保留）
    started = time.monotonic()
    assert remaining_run_seconds(3600.0, started) > 3500


def test_agent_config_caps_disabled():
    cfg = load_document(_AGENT_CONFIG)
    agent = cfg["agent"]
    assert agent["max_iterations"] == 0, "max_iterations 应为 0（禁用）"
    assert agent["timeouts"]["run_seconds"] == 0.0, "run_seconds 应为 0.0（禁用）"


def test_agent_config_layer3_ceiling_configured():
    """第 3 层天花板在配置中就位：max_budget_usd + 费率。"""
    cfg = load_document(_AGENT_CONFIG)
    agent = cfg["agent"]
    assert agent["max_budget_usd"] is not None and agent["max_budget_usd"] > 0
    pricing = agent["budget_pricing"]
    assert pricing["input_per_mtok"] > 0 and pricing["output_per_mtok"] > 0


