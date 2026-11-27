from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from prismclaw.engine.execution.capabilities.catalog import (
    RuntimeCatalog,
)
from prismclaw.engine.execution.request._prompt_types import (
    AvailableSubagentEntry,
    MarketplaceSummary,
)
from prismclaw.engine.execution.request.system_prompt import (
    _subagent_compact_description,
)
from prismclaw.runtime.skills.eligibility import is_model_visible

logger = logging.getLogger(__name__)


def _tool_prompt_summaries(
    *,
    capability_catalog: RuntimeCatalog,
    visible_names: set[str],
    subagent_depth: int = 0,
) -> list[tuple[str, str]]:
    """``<tools>`` 段的工具摘要，数据源是本轮可见集。

    ``visible_names`` 取自 ``TurnPlan.toolset``，与实发 ``tools_for_llm``
    同源，因此提示词不可能宣传模型调不到的工具（SDD D14）。
    """
    return [
        (entry.llm_name, entry.description)
        for entry in capability_catalog.list_visible_tool_entries(
            visible_names=visible_names,
            subagent_depth=subagent_depth,
        )
    ]


def _subagent_catalog_state(catalog: RuntimeCatalog | None) -> int:
    if catalog is None:
        return 0
    return len(catalog.list_agent_entries())


def _build_available_skills_catalog(
    *,
    capability_catalog: RuntimeCatalog | None,
    workspace_path: str | None,
    tool_summaries: Sequence[tuple[str, str]],
) -> tuple[tuple[str, ...], tuple[MarketplaceSummary, ...]]:
    """项目技能名 + 专家包索引（三层披露的第一层）。

    - 项目技能：逐个列名。用户主动安装、数量有界，全量保留。
    - 专家包：只聚合包名 / 外层描述 / 技能计数；子专家包与具体技能不进入
      prompt，由 ``skill_search`` 检索、``activate_skill`` 按名激活。
    - user_only / disabled 技能对模型不可见，两个集合都排除。

    Returns ``(project_skill_names, marketplace_summaries)``。
    """
    if capability_catalog is None or not workspace_path:
        return (), ()
    tool_names = {name for name, _desc in tool_summaries}
    if "skill_search" not in tool_names:
        return (), ()
    try:
        entries = capability_catalog.list_skill_entries()
    except Exception:
        logger.warning("available_skills_catalog.list_skills_failed", exc_info=True)
        return (), ()

    project_names: list[str] = []
    by_expert: dict[str, list[Any]] = {}
    for entry in entries:
        if not is_model_visible(entry.manifest):
            continue
        if entry.expert_name:
            by_expert.setdefault(entry.expert_name, []).append(entry)
        else:
            project_names.append(entry.name)

    summaries = tuple(
        MarketplaceSummary(
            name=name,
            description=next(
                (e.expert_description for e in group if e.expert_description),
                "",
            )
            or "",
            skill_count=len(group),
        )
        for name, group in sorted(by_expert.items())
    )
    return tuple(sorted(project_names)), summaries


def _build_available_subagents_catalog(
    *,
    capability_catalog: RuntimeCatalog | None,
    tool_summaries: Sequence[tuple[str, str]],
) -> tuple[AvailableSubagentEntry, ...]:
    """子代理目录（全量，不截断）。

    单一数据源：``RuntimeCatalog.list_agent_entries()``，与派发时
    校验使用同一注册表。专家包子代理保留在目录里供 ``subagent_search`` 检索
    与 ``<packages>`` 计数，但名称不进入固定 prompt。
    """
    if capability_catalog is None:
        return ()
    tool_names = {name for name, _desc in tool_summaries}
    if "sub_agent" not in tool_names:
        return ()
    try:
        catalog_entries = capability_catalog.list_agent_entries()
    except Exception:
        logger.warning("available_subagents_catalog.list_agents_failed", exc_info=True)
        return ()
    return tuple(
        AvailableSubagentEntry(
            name=entry.name,
            description=_subagent_compact_description(entry.description),
            expert_name=entry.expert_name,
            expert_description=entry.expert_description,
        )
        for entry in sorted(catalog_entries, key=lambda e: e.name)
    )
