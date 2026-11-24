from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from prismclaw.engine.execution.capabilities.catalog import (
    AgentCatalogEntry,
    CapabilityCatalogService,
    ToolCatalogEntry,
)
from prismclaw.schema.services.skills import SkillInvocationState

_QUERY_TOKEN_RE = re.compile(r"[a-z0-9_:+.-]+|[\u4e00-\u9fff]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_EXCLUDED_TOOL_NAMES = frozenset(
    {
        "tool_search",
        "skill_search",
        "subagent_search",
    }
)


def _tokenize_query(value: str) -> list[str]:
    """分词：ASCII 词原样保留；中文按 2-gram 切分（单字保留），
    否则纯中文 query 会得到空 token 列表。"""
    tokens: list[str] = []
    for raw in _QUERY_TOKEN_RE.findall(value.lower()):
        if _CJK_RE.fullmatch(raw):
            if len(raw) == 1:
                tokens.append(raw)
            else:
                tokens.extend(raw[i : i + 2] for i in range(len(raw) - 1))
        elif raw:
            tokens.append(raw)
    return tokens


def _score_text(query_tokens: list[str], *parts: str) -> int:
    if not query_tokens:
        return 0
    haystack = " ".join(part.strip().lower() for part in parts if part and part.strip())
    if not haystack:
        return 0
    score = 0
    for token in query_tokens:
        if token in haystack:
            score += 1
        if token == haystack:
            score += 4
        elif haystack.startswith(token):
            score += 2
    return score


@dataclass(frozen=True)
class ToolDiscoveryEntry:
    name: str
    canonical_name: str
    description: str
    tool_class: str
    effect_class: str
    score: int


@dataclass(frozen=True)
class SkillDiscoveryEntry:
    name: str
    description: str
    location: str
    score: int
    marketplace_name: str | None = None
    marketplace_description: str | None = None
    plugin_name: str | None = None


@dataclass(frozen=True)
class SubagentDiscoveryEntry:
    name: str
    description: str
    package: str | None
    sub_plugin: str | None
    score: int
    # 来源分组：核心子代理（工作区定义）与插件子代理，渲染不混排。
    is_core: bool = False


class ToolDiscoveryRuntime:
    def __init__(self, *, capability_catalog: CapabilityCatalogService) -> None:
        self._catalog = capability_catalog

    def visible_surface(
        self,
        *,
        skill_invocation: SkillInvocationState | None = None,
        discovered_tools: set[str] | None = None,
        subagent_depth: int = 0,
        headless: bool = False,
    ) -> list[ToolCatalogEntry]:
        return self._catalog.list_searchable_tool_entries(
            skill_invocation=skill_invocation,
            discovered_tools=discovered_tools,
            subagent_depth=subagent_depth,
            headless=headless,
        )

    def search_visible_surface(
        self,
        query: str,
        visible_surface: list[ToolCatalogEntry],
        *,
        limit: int = 5,
    ) -> list[ToolDiscoveryEntry]:
        tokens = _tokenize_query(query)
        matches: list[ToolDiscoveryEntry] = []
        for entry in visible_surface:
            if entry.canonical_name in _EXCLUDED_TOOL_NAMES:
                continue
            score = _score_text(
                tokens,
                entry.llm_name,
                entry.canonical_name,
                entry.description,
            )
            if score <= 0:
                continue
            matches.append(
                ToolDiscoveryEntry(
                    name=entry.llm_name,
                    canonical_name=entry.canonical_name,
                    description=entry.description,
                    tool_class=entry.tool_class,
                    effect_class=entry.effect_class,
                    score=score,
                )
            )
        matches.sort(key=lambda item: (-item.score, item.name))
        return matches[:limit]

    def search(
        self,
        query: str,
        *,
        skill_invocation: SkillInvocationState | None = None,
        discovered_tools: set[str] | None = None,
        subagent_depth: int = 0,
        limit: int = 5,
    ) -> list[ToolDiscoveryEntry]:
        return self.search_visible_surface(
            query,
            self.visible_surface(
                skill_invocation=skill_invocation,
                discovered_tools=discovered_tools,
                subagent_depth=subagent_depth,
            ),
            limit=limit,
        )


class SkillDiscoveryRuntime:
    def __init__(
        self,
        *,
        capability_catalog: CapabilityCatalogService,
    ) -> None:
        self._catalog = capability_catalog

    def search(
        self,
        query: str,
        *,
        workspace_path: Path,
        limit: int = 5,
    ) -> list[SkillDiscoveryEntry]:
        """搜索技能。全量匹配，返回 name + description（200 字截断），不返回 instructions。

        marketplace_name 参与评分，这样搜索插件名也能匹配到该插件下的技能。
        instructions 仅在技能激活时由 SkillRuntime 按需加载。
        """
        tokens = _tokenize_query(query)
        matches: list[SkillDiscoveryEntry] = []
        for skill in self._catalog.list_skill_entries(workspace_path):
            # marketplace_name + marketplace_description + plugin_name 参与匹配，
            # 搜索"插件包中文名"能命中 marketplace_description="综合插件包"下的技能，
            # 搜索子插件名也能匹配该子插件下的技能
            score = _score_text(
                tokens,
                skill.name,
                getattr(skill.manifest, "name_zh", None) or "",
                skill.description,
                skill.marketplace_name or "",
                skill.marketplace_description or "",
                skill.plugin_name or "",
            )
            if score <= 0:
                continue
            matches.append(
                SkillDiscoveryEntry(
                    name=skill.name,
                    description=skill.description[:200],
                    location=skill.location,
                    score=score,
                    marketplace_name=skill.marketplace_name,
                    marketplace_description=skill.marketplace_description,
                    plugin_name=skill.plugin_name,
                )
            )
        matches.sort(key=lambda item: (-item.score, item.name))
        return matches[:limit]

    def list_all(
        self,
        *,
        workspace_path: Path,
        limit: int = 40,
    ) -> list[SkillDiscoveryEntry]:
        """无关键词兜底：返回全部技能（0 结果时由 SkillSearchTool 调用，避免模型盲目重试）。"""
        entries: list[SkillDiscoveryEntry] = []
        for skill in self._catalog.list_skill_entries(workspace_path):
            entries.append(
                SkillDiscoveryEntry(
                    name=skill.name,
                    description=skill.description[:200],
                    location=skill.location,
                    score=0,
                    marketplace_name=skill.marketplace_name,
                    marketplace_description=skill.marketplace_description,
                    plugin_name=skill.plugin_name,
                )
            )
        entries.sort(key=lambda item: item.name)
        return entries[:limit]


class SubagentDiscoveryRuntime:
    """子代理检索——固定 prompt 只披露核心子代理索引，其余按需检索。

    数据源与派发校验同源（``CapabilityCatalogService.list_agent_entries``）。
    候选集 = 核心子代理 + 插件子代理：核心子代理虽然已在 ``<subagents>``
    列出，但当模型记不清名称、或固定段被预算撤除时，检索必须仍能命中，
    否则没有替代通道（SDD D7）。``is_core`` 仅用于渲染分组，各自配额。
    """

    _DESC_LIMIT = 200

    def __init__(self, *, capability_catalog: CapabilityCatalogService) -> None:
        self._catalog = capability_catalog

    def search(
        self,
        query: str,
        *,
        package: str | None = None,
        limit: int = 10,
        active_plugins: frozenset[str] | set[str] | None = None,
    ) -> list[SubagentDiscoveryEntry]:
        tokens = _tokenize_query(query)
        package_key = package.strip().lower() if package else None
        matches: list[SubagentDiscoveryEntry] = []
        for agent, is_core in self._candidates(active_plugins):
            if package_key is not None and package_key not in {
                (agent.marketplace_name or "").lower(),
                (agent.plugin_name or "").lower(),
            }:
                continue
            score = _score_text(
                tokens,
                agent.name,
                agent.description,
                agent.marketplace_name or "",
                agent.marketplace_description or "",
                agent.plugin_name or "",
            )
            if score <= 0:
                continue
            matches.append(self._entry(agent, score=score, is_core=is_core))
        matches.sort(key=lambda item: (-item.score, item.name))
        return matches[: self._clamp_limit(limit)]

    def list_agents(
        self,
        *,
        limit: int = 20,
        package: str | None = None,
        active_plugins: frozenset[str] | set[str] | None = None,
    ) -> list[SubagentDiscoveryEntry]:
        """无关键词兜底：列出全部候选（核心 + 插件），避免模型盲目重试。"""
        package_key = package.strip().lower() if package else None
        entries = [
            self._entry(agent, score=0, is_core=is_core)
            for agent, is_core in self._candidates(active_plugins)
            if package_key is None
            or package_key
            in {
                (agent.marketplace_name or "").lower(),
                (agent.plugin_name or "").lower(),
            }
        ]
        entries.sort(key=lambda item: (not item.is_core, item.name))
        return entries[: self._clamp_limit(limit)]

    def _candidates(
        self, active_plugins: frozenset[str] | set[str] | None = None
    ) -> list[tuple[Any, bool]]:
        """核心 + 插件候选。插件候选受激活作用域约束，核心不受。"""
        result: list[tuple[Any, bool]] = []
        for entry in self._catalog.list_agent_entries():
            if entry.plugin_name is None:
                result.append((entry, True))
            elif active_plugins is None or entry.plugin_name in active_plugins:
                result.append((entry, False))
        return result

    def _entry(
        self, agent: AgentCatalogEntry, *, score: int, is_core: bool
    ) -> SubagentDiscoveryEntry:
        return SubagentDiscoveryEntry(
            name=agent.name,
            description=(agent.description or "")[: self._DESC_LIMIT],
            package=agent.marketplace_name,
            sub_plugin=agent.plugin_name,
            score=score,
            is_core=is_core,
        )

    @staticmethod
    def _clamp_limit(limit: int) -> int:
        return max(1, min(int(limit), 20))
