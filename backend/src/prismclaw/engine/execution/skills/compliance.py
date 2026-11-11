"""Workflow compliance computation.

Moved from contracts.services.skills to break the contracts→engine
dependency cycle. This is a pure computation function that belongs in
the engine layer alongside its consumers.
"""
from __future__ import annotations

from typing import Any


def compute_workflow_compliance(
    declared_sequence: list[str] | None,
    actual_sequence: list[str] | None,
) -> dict[str, Any]:
    """对单条工作流技能的声明序列 ↔ 实际调用序列做偏差比对。

    输入:
      declared_sequence: 加载期 parser 预提取的子技能声明名有序列表(可含重复)
      actual_sequence:   执行期收集的实际调用 skill name 有序列表(可含重复)

    返回 dict (JSON-encodable,直接传给 telemetry.emit 的 fields):
        declared_count:  int
        actual_count:    int
        missing:         list[str]   — declared 独有的去重名称
        extra:           list[str]   — actual 独有的去重名称
        order_match:     bool        — declared 名称在 actual 中按声明顺序出现(允许间隔)
        compliance_ratio:float       — 0.0 .. 1.0; declared 为空时视为 trivially 合规 = 1.0
    """
    declared = list(declared_sequence or [])
    actual = list(actual_sequence or [])

    declared_set = set(declared)
    actual_set = set(actual)

    missing = sorted(declared_set - actual_set)
    extra = sorted(actual_set - declared_set)

    # --- order_match:declared 是 actual 的"按顺序子序列"(允许 actual 中间插入其他调用)
    order_match: bool
    if not declared:
        order_match = True
    else:
        it = iter(actual)
        order_match = all(any(a == d for a in it) for d in declared)

    # --- compliance_ratio:按名称去重后,命中的 declared 元素数 / declared 元素数
    if declared_set:
        compliance_ratio = round(len(declared_set & actual_set) / len(declared_set), 4)
    else:
        compliance_ratio = 1.0

    return {
        "declared_count": len(declared),
        "actual_count": len(actual),
        "missing": missing,
        "extra": extra,
        "order_match": order_match,
        "compliance_ratio": compliance_ratio,
    }
