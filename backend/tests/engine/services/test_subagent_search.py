"""subagent_search：插件子代理逐级披露契约。

固定 prompt 只列核心子代理与包索引；插件子代理必须能通过本工具检索，
且工作区核心子代理不重复出现在结果里。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from prismclaw.engine.toolkit.registry.discovery import SubagentSearchTool


def _agent(
    name: str,
    description: str,
    *,
    plugin_name: str | None = None,
    marketplace_name: str | None = None,
):
    return SimpleNamespace(
        name=name,
        description=description,
        plugin_name=plugin_name,
        marketplace_name=marketplace_name,
        marketplace_description="领域能力包" if marketplace_name else None,
    )


class _FakeCatalog:
    def __init__(self, entries):
        self._entries = entries

    def list_agent_entries(self):
        return self._entries


_CATALOG = _FakeCatalog([
    _agent("architect", "架构规划"),
    _agent("auditor", "结果验证"),
    _agent(
        "docket-watcher",
        "监控活跃案件组合中的法院案卷",
        plugin_name="litigation",
        marketplace_name="legal-chineselaw",
    ),
    _agent(
        "clinical-reviewer",
        "临床病历与检查数据复核",
        plugin_name="clinical",
        marketplace_name="medical-healthcare",
    ),
])


def _call(args):
    tool = SubagentSearchTool(capability_catalog=_CATALOG)
    result = asyncio.run(tool.execute(args, context=None))
    return result.content[0].text


def test_returns_plugin_subagent_and_excludes_core():
    text = _call({"query": "案卷 监控"})
    assert "docket-watcher" in text
    assert "`architect`" not in text


def test_package_filter():
    assert "clinical-reviewer" in _call({"query": "复核", "package": "medical-healthcare"})
    assert "docket-watcher" not in _call({"query": "案卷", "package": "medical-healthcare"})


def test_no_match_falls_back_to_all_plugin_agents():
    text = _call({"query": "完全不相关的查询xyz"})
    assert "docket-watcher" in text
    assert "clinical-reviewer" in text
    assert "`architect`" not in text
    assert "subagent_search" in text


def test_limit_is_clamped():
    text = _call({"query": "案件 临床", "limit": 99})
    assert "docket-watcher" in text
    assert "clinical-reviewer" in text


def test_limit_caps_result_count():
    entries = [
        _agent(f"plugin-agent-{i}", "案件 复核", plugin_name="p", marketplace_name="mp")
        for i in range(25)
    ]
    tool = SubagentSearchTool(capability_catalog=_FakeCatalog(entries))
    result = asyncio.run(tool.execute({"query": "案件", "limit": 99}, context=None))
    text = result.content[0].text
    assert text.count("plugin-agent-") == 20


def test_missing_query_is_error():
    tool = SubagentSearchTool(capability_catalog=_CATALOG)
    result = asyncio.run(tool.execute({"query": "  "}, context=None))
    assert result.is_error
