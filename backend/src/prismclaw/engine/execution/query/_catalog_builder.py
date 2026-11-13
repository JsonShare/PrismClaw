from __future__ import annotations

import logging
from typing import Any

from prismclaw.engine.execution.capabilities.catalog import (
    CapabilityCatalogService,
)
from prismclaw.engine.execution.request.system_prompt import (
    AvailableSubagentEntry,
    MarketplaceSummary,
    _subagent_compact_description,
)
from prismclaw.engine.subagent.runtime.follow_up import (
    build_session_follow_up_note,
    session_follow_up_snapshot,
)
from prismclaw.schema.agent.types import WorkspaceMode
from prismclaw.schema.services.skills import SkillInvocationState

logger = logging.getLogger(__name__)

def _session_follow_up_notes(tree: Any, tool_summaries: list[tuple[str, str]]) -> list[str]:
    return build_session_follow_up_note(
        session_follow_up_snapshot(tree),
        available_tools={name for name, _desc in tool_summaries},
    )


def _tool_prompt_summaries(
    *,
    capability_catalog: CapabilityCatalogService,
    skill_invocation: SkillInvocationState | None,
    discovered_tools: set[str] | None,
    mode: WorkspaceMode,
    subagent_depth: int,
    headless: bool = False,
) -> list[tuple[str, str]]:
    return [
        (entry.llm_name, entry.description)
        for entry in capability_catalog.list_visible_tool_entries(
            skill_invocation=skill_invocation,
            discovered_tools=discovered_tools,
            mode=mode,
            subagent_depth=subagent_depth,
            headless=headless,
        )
    ]


def _subagent_catalog_state(catalog: CapabilityCatalogService | None) -> tuple[int, bool]:
    if catalog is None:
        return 0, False
    entries = catalog.list_agent_entries()
    return len(entries), any(entry.can_handoff_to for entry in entries)


def _build_available_skills_catalog(
    *,
    capability_catalog: CapabilityCatalogService | None,
    workspace_path: str | None,
    tool_summaries: list[tuple[str, str]],
) -> "tuple[tuple[str, ...], tuple[MarketplaceSummary, ...]]":
    """项目技能名 + 插件包索引（三层披露的第一层）。

    - 项目技能：逐个列名。用户主动安装、数量有界，全量保留。
    - 插件包：只聚合包名 / 外层描述 / 技能计数；子插件与具体技能不进入
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
        entries = capability_catalog.list_skill_entries(str(workspace_path))
    except Exception:
        logger.warning("available_skills_catalog.list_skills_failed", exc_info=True)
        return (), ()

    project_names: list[str] = []
    by_marketplace: dict[str, list[Any]] = {}
    for entry in entries:
        manifest = entry.manifest
        # invocation_policy 过滤：
        # - AUTO / MODEL_ONLY → 模型可见
        # - USER_ONLY → 仅用户指定可见，不出现在模型列表
        # - DISABLED → 完全禁用，不出现在任何列表
        policy = getattr(manifest, "invocation_policy", None)
        policy_str = str(policy.value) if policy is not None else "auto"
        if policy_str in ("user_only", "disabled"):
            continue
        if entry.marketplace_name:
            by_marketplace.setdefault(entry.marketplace_name, []).append(entry)
        else:
            project_names.append(entry.name)

    summaries = tuple(
        MarketplaceSummary(
            name=name,
            description=next(
                (e.marketplace_description for e in group if e.marketplace_description),
                "",
            )
            or "",
            skill_count=len(group),
        )
        for name, group in sorted(by_marketplace.items())
    )
    return tuple(sorted(project_names)), summaries


def _build_available_subagents_catalog(
    *,
    capability_catalog: CapabilityCatalogService | None,
    tool_summaries: list[tuple[str, str]],
) -> tuple[AvailableSubagentEntry, ...]:
    """子代理目录（全量，不截断）。

    单一数据源：``CapabilityCatalogService.list_agent_entries()``，与派发时
    校验使用同一注册表。插件子代理保留在目录里供 ``subagent_search`` 检索
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
            plugin_name=entry.plugin_name,
            marketplace_name=entry.marketplace_name,
            marketplace_description=entry.marketplace_description,
        )
        for entry in sorted(catalog_entries, key=lambda e: e.name)
    )
