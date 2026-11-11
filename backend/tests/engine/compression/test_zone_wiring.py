"""批 4b-1：区域接线统一入口 + 前置约束 + clamp 契约测试。"""
from __future__ import annotations

import inspect

import prismclaw.engine.execution.request.packing as packing_module
from prismclaw.engine.compression.context_zones import (
    clamp_keep_recent,
    clamp_summary_cap,
    resolve_context_zones,
    resolve_runtime_zones,
)
from prismclaw.schema.config.config import ContextBudgetConfig


def test_absolute_passthrough():
    budget = ContextBudgetConfig(mode="absolute", window_tokens=131072)
    zones = resolve_runtime_zones(
        budget=budget,
        window=None,
        model_max_output=24576,
        tools_wire=6588,
    )
    assert zones.system == budget.system_zone_tokens
    assert zones.dynamic == budget.dynamic_zone_tokens
    assert zones.result == budget.dynamic_zone_tokens
    assert zones.tools == 6588
    assert zones.history == 0
    assert zones.input_budget == budget.window_tokens
    assert zones.output_reserve == (budget.output_reserve_tokens or 0)


def test_runtime_zones_ratio():
    budget = ContextBudgetConfig(mode="ratio")
    runtime = resolve_runtime_zones(
        budget=budget,
        window=200000,
        model_max_output=30000,
        tools_wire=6588,
        image_tokens=0,
    )
    baseline = resolve_context_zones(
        window=200000,
        model_max_output=30000,
        budget=budget,
        tools_wire=6588,
        image_tokens=0,
    )
    assert runtime == baseline


def test_packing_no_tool_projection():
    src = inspect.getsource(packing_module)
    assert "tool_projection" not in src


def test_clamp_helpers():
    assert clamp_keep_recent(20000, 40000) == 20000
    assert clamp_keep_recent(30000, 40000) == 20000  # 0.5 * 40000
    assert clamp_summary_cap(20000, 40000) == 16000  # 0.4 * 40000
    assert clamp_summary_cap(10000, 40000) == 10000