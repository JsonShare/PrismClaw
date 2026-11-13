"""批 4b-2：历史压缩触发 + 工具数组 append_only + 图像 token 估计测试。"""
from __future__ import annotations

from types import SimpleNamespace

from prismclaw.engine.compression.budget_orchestrator import BudgetOrchestrator
from prismclaw.schema.config.config import AgentRunConfig, ContextBudgetConfig, VisionConfig


def test_absolute_zero_drift():
    orch = BudgetOrchestrator()
    # 判定以 history_tokens>history_budget 为准，last_prompt_tokens 不参与判定。
    a = orch.assess_history_compaction(
        history_tokens=12000, history_budget=10000, last_prompt_tokens=999999,
    )
    assert a.should_compact is True
    b = orch.assess_history_compaction(
        history_tokens=5000, history_budget=10000, last_prompt_tokens=999999,
    )
    assert b.should_compact is False
    c = orch.assess_history_compaction(history_tokens=5000, history_budget=0)
    assert c.should_compact is False


def test_max_tokens_clamp_window_geometry():
    from prismclaw.engine.services.llm.client import compute_max_tokens_clamp

    # 钳制只看窗口几何：max_tokens ≤ window − est_input − headroom。
    clamped, est = compute_max_tokens_clamp(
        model_max_tokens=32768,
        context_window=131072,
        estimated_input_tokens=100_000,
    )
    assert clamped == 131072 - 100_000 - 1024
    assert est == 100_000
    # 无窗口信息时不钳制。
    clamped2, _ = compute_max_tokens_clamp(
        model_max_tokens=32768,
        context_window=None,
        estimated_input_tokens=100_000,
    )
    assert clamped2 == 32768


def test_append_only_order():
    from prismclaw.engine.execution.query.pre_request_pipeline import (
        order_visible_tools_append_only,
    )

    def tool(name):
        return SimpleNamespace(name=name)

    # 首轮：无发现，保持 registry 排序
    first = [tool("a"), tool("b"), tool("c")]
    ordered1 = order_visible_tools_append_only(
        sorted_visible_tools=first, discovered_ordered_names=[]
    )
    assert [t.name for t in ordered1] == ["a", "b", "c"]

    # 第二轮：多一个 discovered 工具 d，核心相对顺序不变，d 追加到末尾
    second = [tool("a"), tool("b"), tool("c"), tool("d")]
    ordered2 = order_visible_tools_append_only(
        sorted_visible_tools=second, discovered_ordered_names=["d"]
    )
    assert [t.name for t in ordered2] == ["a", "b", "c", "d"]

    # 即使 registry 排序把 discovered 工具排到最前，也仍应追加到末尾
    third = [tool("d"), tool("a"), tool("b"), tool("c")]
    ordered3 = order_visible_tools_append_only(
        sorted_visible_tools=third, discovered_ordered_names=["d"]
    )
    assert [t.name for t in ordered3] == ["a", "b", "c", "d"]


def test_estimate_tokens_images():
    import prismclaw.schema.shared.utils as u

    k = 3
    text = "\n".join([u.IMAGE_PLACEHOLDER] * k)
    zero = u.estimate_tokens(text, image_tokens_each=0)
    counted = u.estimate_tokens(text, image_tokens_each=1024)
    assert counted >= 1024 * k
    assert zero < counted
    # 每张图按 1024 计，扣除极少占位符文本估算后，差值仍接近 1024*k
    assert counted - zero >= 1000 * k


def test_config_defaults():
    cfg = ContextBudgetConfig()
    assert cfg.window_tokens == 131_072
    run = AgentRunConfig()
    assert run.tool_mutation_policy == "append_only"
    assert VisionConfig().tokens_per_image == 1024
    assert VisionConfig().max_images_inline == 4