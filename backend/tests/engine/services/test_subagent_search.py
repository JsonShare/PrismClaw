"""subagent_search 检索契约。

固定 prompt 只列核心子代理索引；检索候选 = 核心 + 专家包（核心是兜底通道，
索引缺失或被预算撤除时必须仍能命中）。渲染按来源分组，不混排。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast

from prismclaw.engine.execution.capabilities.catalog import (
    AgentCatalogEntry,
    RuntimeCatalog,
)
from prismclaw.engine.toolkit.registry.discovery import SubagentSearchTool
from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext
from prismclaw.schema.agent.data_types import TextBlock


def _agent(
    name: str,
    description: str,
    *,
    expert_name: str | None = None,
) -> AgentCatalogEntry:
    return AgentCatalogEntry(
        name=name,
        description=description,
        manifest=None,
        expert_name=expert_name,
        expert_description="领域能力包" if expert_name else None,
    )


class _FakeCatalog:
    def __init__(self, entries: list[AgentCatalogEntry]) -> None:
        self._entries = entries

    def list_agent_entries(self) -> list[AgentCatalogEntry]:
        return self._entries


def _tool(catalog: _FakeCatalog) -> SubagentSearchTool:
    return SubagentSearchTool(capability_catalog=cast(RuntimeCatalog, catalog))


_CATALOG = _FakeCatalog(
    [
        _agent("architect", "架构规划"),
        _agent("auditor", "结果验证"),
        _agent(
            "docket-watcher",
            "监控活跃案件组合中的法院案卷",
            expert_name="legal-chineselaw",
        ),
        _agent(
            "clinical-reviewer",
            "临床病历与检查数据复核",
            expert_name="medical-healthcare",
        ),
    ]
)


def _call(args: dict[str, Any]) -> str:
    result = asyncio.run(
        _tool(_CATALOG).execute(
            args, context=ToolContext(workspace_id="w", workspace_path=Path("/tmp"))
        )
    )
    block = result.content[0]
    assert isinstance(block, TextBlock)
    return block.text


def test_returns_expert_subagent() -> None:
    text = _call({"query": "案卷 监控"})
    assert "docket-watcher" in text
    assert "专家包子代理" in text


def test_core_subagent_is_searchable() -> None:
    """D7：核心子代理必须能被检索命中，不能因"已在 <subagents>"而无兜底。"""
    text = _call({"query": "架构规划"})
    assert "`architect`" in text
    assert "核心子代理" in text


def test_core_and_expert_rendered_in_separate_groups() -> None:
    text = _call({"query": "复核 架构"})
    assert "### 核心子代理：" in text
    assert "### 专家包子代理：" in text
    assert text.index("### 核心子代理：") < text.index("### 专家包子代理：")


def test_package_filter() -> None:
    assert "clinical-reviewer" in _call({"query": "复核", "package": "medical-healthcare"})
    assert "docket-watcher" not in _call({"query": "案卷", "package": "medical-healthcare"})


def test_no_match_falls_back_to_all_agents() -> None:
    text = _call({"query": "完全不相关的查询xyz"})
    assert "docket-watcher" in text
    assert "clinical-reviewer" in text
    assert "`architect`" in text
    assert "subagent_search" in text


def test_limit_is_clamped() -> None:
    text = _call({"query": "案件 临床", "limit": 99})
    assert "docket-watcher" in text
    assert "clinical-reviewer" in text


def test_limit_caps_result_count() -> None:
    entries = [
        _agent(f"expert-agent-{i}", "案件 复核", expert_name="mp")
        for i in range(25)
    ]
    result = asyncio.run(
        _tool(_FakeCatalog(entries)).execute(
            {"query": "案件", "limit": 99},
            context=ToolContext(workspace_id="w", workspace_path=Path("/tmp")),
        )
    )
    block = result.content[0]
    assert isinstance(block, TextBlock)
    assert block.text.count("expert-agent-") == 20


def test_missing_query_is_error() -> None:
    result = asyncio.run(
        _tool(_CATALOG).execute({"query": "  "}, context=cast(ToolContext, None))
    )
    assert result.is_error
