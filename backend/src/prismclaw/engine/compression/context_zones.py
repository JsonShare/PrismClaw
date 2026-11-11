"""比例模式（v4 §3.2）的区域求解器。

纯函数：无 IO、无 settings 依赖，全部入参显式传入，便于单测。
只实现 plan 阶段的配比求解；render/reconcile 阶段在批 4b/4.3 接入。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from prismclaw.schema.config.config import ContextBudgetConfig


@dataclass(frozen=True)
class ContextZones:
    """比例模式一次求解的分区结果。

    字段语义（v4 §3.2）：output_reserve=O；system=S；tools=T（分配值）；
    dynamic=D；result=R；history=H；input_budget=A_eff。
    """

    output_reserve: int
    system: int
    tools: int
    dynamic: int
    result: int
    history: int
    input_budget: int


def _clamp(x: int, lo: int, hi: int) -> int:
    return min(hi, max(x, lo))


def resolve_context_zones(
    *,
    window: int,
    model_max_output: int | None,
    budget: "ContextBudgetConfig",
    tools_wire: int,
    image_tokens: int = 0,
) -> ContextZones:
    """按 v4 §3.2 求解各分区预算。

    公式（修正版）：
      O     = min( max(model_max_output or 0, round(0.15·W)), round(0.30·W), zones.output.max )
      A     = W − O
      A_eff = A − image_tokens
      S     = clamp(round(system.ratio·A_eff), system.floor, system.cap)
      T     = clamp(tools_wire, tools.floor, round(tools.ratio·A_eff))
      D     = clamp(round(dynamic.ratio·A_eff), dynamic.floor, dynamic.cap)
      R     = clamp(round(result.ratio·A_eff), result.floor, result.cap)
      H     = A_eff − S − T − D − R

    普适拒绝门槛：A_eff < S_floor + T_floor + D_floor + R_floor + 2048 时抛
    ``context_window_under_zone_floors``。
    """
    zones = budget.zones

    o = min(
        max(model_max_output or 0, round(0.15 * window)),
        round(0.30 * window),
        zones.output.max,
    )
    a = window - o
    a_eff = a - image_tokens

    floor_sum = (
        zones.system.floor
        + zones.tools.floor
        + zones.dynamic.floor
        + zones.result.floor
        + 2048
    )
    if a_eff < floor_sum:
        raise ValueError("context_window_under_zone_floors")

    s = _clamp(round(zones.system.ratio * a_eff), zones.system.floor, zones.system.cap)
    t = _clamp(tools_wire, zones.tools.floor, round(zones.tools.ratio * a_eff))
    d = _clamp(round(zones.dynamic.ratio * a_eff), zones.dynamic.floor, zones.dynamic.cap)
    r = _clamp(round(zones.result.ratio * a_eff), zones.result.floor, zones.result.cap)
    h = a_eff - s - t - d - r

    return ContextZones(
        output_reserve=o,
        system=s,
        tools=t,
        dynamic=d,
        result=r,
        history=h,
        input_budget=a_eff,
    )


def resolve_runtime_zones(
    *,
    budget: "ContextBudgetConfig",
    window: int | None,
    model_max_output: int | None,
    tools_wire: int,
    image_tokens: int = 0,
) -> ContextZones:
    """统一分区入口：ratio 转发 resolve_context_zones；absolute 回落固定四键。

    - ratio：window 取入参优先，缺失回退 ``budget.window_tokens``，其余照转。
    - absolute：输出满足「与现状完全等价」——system/dynamic/result 来自固定
      四键，tools 取传入 wire，input_budget 填 ``budget.window_tokens``；
      history 填 0（absolute 模式历史预算由 ``compute_history_budget`` 计算，
      本入口不覆盖）。
    """
    if budget.mode == "ratio":
        return resolve_context_zones(
            window=window if window is not None else int(budget.window_tokens),
            model_max_output=model_max_output,
            budget=budget,
            tools_wire=tools_wire,
            image_tokens=image_tokens,
        )
    return ContextZones(
        output_reserve=int(budget.output_reserve_tokens or 0),
        system=int(budget.system_zone_tokens),
        tools=int(tools_wire),
        dynamic=int(budget.dynamic_zone_tokens),
        result=int(budget.dynamic_zone_tokens),
        # absolute 模式下历史预算由 compute_history_budget 计算，本入口不覆盖。
        history=0,
        input_budget=int(budget.window_tokens),
    )


def zone_of(
    budget: "ContextBudgetConfig",
    key: str,
    *,
    window: int | None = None,
    model_max_output: int | None = None,
    tools_wire: int = 0,
) -> int:
    """按模式取单个分区预算，避免调用点到处撒 if-else。

    absolute: system→system_zone_tokens；dynamic/result→dynamic_zone_tokens。
    ratio: 走 resolve_runtime_zones 求解结果。
    """
    if budget.mode != "ratio":
        if key in {"dynamic", "result"}:
            return int(budget.dynamic_zone_tokens)
        return int(budget.system_zone_tokens)
    zones = resolve_runtime_zones(
        budget=budget,
        window=window if window is not None else int(budget.window_tokens),
        model_max_output=model_max_output,
        tools_wire=tools_wire,
    )
    return int(getattr(zones, key))


def clamp_keep_recent(keep_recent_tokens: int, history_budget: int) -> int:
    """4.4.4：保留最近段不超过历史预算的 50%。"""
    return min(max(0, int(keep_recent_tokens)), max(0, int(0.5 * history_budget)))


def clamp_summary_cap(summary_output_token_cap: int, history_budget: int) -> int:
    """4.4.4：压缩摘要输出预留不超过历史预算的 40%。"""
    return min(max(0, int(summary_output_token_cap)), max(0, int(0.4 * history_budget)))