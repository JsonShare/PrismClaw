"""规则段契约 —— 收敛约束与模式中立性。

这些断言保护的是"目标导向收敛"这几条改动的语义：
过度验证、范围外扩、chat/work 矛盾都曾导致任务轮次失控。
"""
from __future__ import annotations

from prismclaw.engine.execution.request.system_prompt import _RULES


def test_verification_is_bounded_by_conclusion() -> None:
    assert "验证只覆盖影响结论的部分" in _RULES
    # 旧措辞"不影响的不验证"给了模型跳过验证的口子，必须已移除
    assert "不影响的不验证" not in _RULES


def test_exploration_has_stop_condition() -> None:
    assert "探索以「足以行动」为界" in _RULES
    assert "能探索先探索" not in _RULES


def test_delivery_aligned_to_goal() -> None:
    assert "交付对齐目标" in _RULES
    assert "达成即停" in _RULES


def test_delivery_clause_is_mode_neutral() -> None:
    """第 9 条必须同时覆盖可写与只读模式，避免 chat 下出现无法履行的交付物要求。"""
    assert "有写权限时交付物只落" in _RULES
    assert "只读模式不产出文件交付物" in _RULES


def test_rules_have_no_domain_specific_wording() -> None:
    """系统定位规则不写死具体业务领域。"""
    for word in ("医疗", "法律", "药学", "临床", "处方", "法规", "代码库"):
        assert word not in _RULES, f"规则段出现领域词: {word}"
