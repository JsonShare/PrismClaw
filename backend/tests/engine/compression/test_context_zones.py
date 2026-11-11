"""批 4a：模型窗口比例的区域求解器（resolve_context_zones）契约测试。

锁定 v4 §4 修正表五种窗口的实算数值，以及：
- image_tokens 扣减对 A_eff / H 的影响；
- 普适拒绝门槛抛 ValueError；
- absolute 模式（纯默认 ContextBudgetConfig）compute_history_budget 行为不变；
- ratio 模式 compute_history_budget 走 resolve_context_zones。
"""
from __future__ import annotations

import pytest

from prismclaw.engine.compression.context_zones import (
    ContextZones,
    resolve_context_zones,
)
from prismclaw.schema.config.config import ContextBudgetConfig


# (window, model_max_output, O, S, T, D, R, H)
CASES = [
    (32768, 6144, 6144, 3195, 4792, 3994, 3994, 10649),
    (65536, 12288, 12288, 6390, 6588, 7987, 7987, 24296),
    (131072, 24576, 24576, 12288, 6588, 15974, 15974, 55672),
    (200000, 30000, 30000, 12288, 6588, 16384, 16384, 118356),
    (1000000, 65536, 65536, 12288, 6588, 16384, 16384, 882820),
]


@pytest.mark.parametrize("window,mo,o,s,t,d,r,h", CASES)
def test_ratio_zones_match_design_table(window, mo, o, s, t, d, r, h):
    budget = ContextBudgetConfig(mode="ratio")
    zones = resolve_context_zones(
        window=window,
        model_max_output=mo,
        budget=budget,
        tools_wire=6588,
    )
    assert isinstance(zones, ContextZones)
    assert zones.output_reserve == o
    assert zones.input_budget == window - o
    assert zones.system == s
    assert zones.tools == t
    assert zones.dynamic == d
    assert zones.result == r
    assert zones.history == h
    assert s + t + d + r + h == zones.input_budget


def test_image_tokens_reduce_a_eff_and_history():
    """200k 窗口下 S/D/R 触顶、T 为实测值，扣 image_tokens 恰好只平移 A_eff 与 H。"""
    budget = ContextBudgetConfig(mode="ratio")
    baseline = resolve_context_zones(
        window=200000, model_max_output=30000, budget=budget, tools_wire=6588,
    )
    with_images = resolve_context_zones(
        window=200000, model_max_output=30000, budget=budget,
        tools_wire=6588, image_tokens=2048,
    )
    assert with_images.input_budget == baseline.input_budget - 2048
    assert with_images.history == baseline.history - 2048
    # 触顶分区不受影响
    assert (with_images.system, with_images.tools, with_images.dynamic, with_images.result) == (
        baseline.system, baseline.tools, baseline.dynamic, baseline.result,
    )


def test_under_zone_floors_raises():
    budget = ContextBudgetConfig(mode="ratio")
    with pytest.raises(ValueError, match="context_window_under_zone_floors"):
        resolve_context_zones(
            window=10000, model_max_output=None, budget=budget, tools_wire=6588,
        )


def test_absolute_mode_compute_history_budget_unchanged():
    """纯默认 ContextBudgetConfig（absolute）走老路径，数值不受新增字段影响。"""
    budget = ContextBudgetConfig()
    assert budget.mode == "absolute"
    # 锁定 absolute 老公式输出：remaining=131072-4096-4096-6588=116292，
    # reserve=min(16384, 116292)=16384（再 clamp 到 116292-4096），
    # floor=max(4096, 116292//4)=29073，uncapped=116292-16384=99908。
    assert budget.compute_history_budget(model_max_output=24576, tools_wire_tokens=6588) == 99908

    dump = budget.model_dump()
    assert dump["mode"] == "absolute"
    assert set(dump["zones"].keys()) == {"system", "dynamic", "result", "tools", "output"}


def test_ratio_mode_compute_history_budget_uses_resolver():
    """ratio 模式 compute_history_budget 返回 resolve_context_zones 的 history（默认 128k 窗口）。"""
    budget = ContextBudgetConfig(mode="ratio")
    assert budget.compute_history_budget(model_max_output=24576, tools_wire_tokens=6588) == 55672