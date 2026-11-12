"""absolute 模式下的分区预算辅助函数。

纯函数：无 IO、无 settings 依赖，全部入参显式传入，便于单测。
ratio 模式已删除（v4 §3.2 配比求解整体移除），本模块只保留 absolute
固定四键的分区映射与夹紧工具函数。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from prismclaw.schema.config.config import ContextBudgetConfig


@dataclass(frozen=True)
class ContextZones:
    """absolute 模式分区结果。

    system/dynamic 来自固定四键；result 复用 dynamic 预算（absolute 模式
    无独立 result 分区）；tools 取传入 wire。
    """

    system: int
    tools: int
    dynamic: int
    result: int


def resolve_runtime_zones(
    *,
    budget: "ContextBudgetConfig",
    tools_wire: int,
) -> ContextZones:
    """absolute 模式分区入口。"""
    return ContextZones(
        system=int(budget.system_zone_tokens),
        tools=int(tools_wire),
        dynamic=int(budget.dynamic_zone_tokens),
        result=int(budget.dynamic_zone_tokens),
    )


def zone_of(budget: "ContextBudgetConfig", key: str) -> int:
    """取单个分区预算（absolute 模式）。

    dynamic/result → dynamic_zone_tokens；其余 → system_zone_tokens。
    """
    if key in {"dynamic", "result"}:
        return int(budget.dynamic_zone_tokens)
    return int(budget.system_zone_tokens)


def clamp_keep_recent(keep_recent_tokens: int, history_budget: int) -> int:
    """4.4.4：保留最近段不超过历史预算的 50%。"""
    return min(max(0, int(keep_recent_tokens)), max(0, int(0.5 * history_budget)))


def clamp_summary_cap(summary_output_token_cap: int, history_budget: int) -> int:
    """4.4.4：压缩摘要输出预留不超过历史预算的 40%。"""
    return min(max(0, int(summary_output_token_cap)), max(0, int(0.4 * history_budget)))
