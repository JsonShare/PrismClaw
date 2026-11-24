"""插件作用域门控：四个闸门 + allowed_tools 收敛 + 粘性/替换语义。

对应 docs/architecture/plugin-scope-gate-sdd-v3.md §五 的门控断言与回归断言。
不变量：未激活插件的一切能力都不可达；``tool_scope is None`` 表示无插件上下文，
不做任何限制（headless / 测试 / 装配未就绪）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from prismclaw.engine.execution.capabilities.discovery_runtime import (
    SubagentDiscoveryRuntime,
)
from prismclaw.engine.execution.dispatch.execution_entry import (
    execute_preflighted_tool_calls,
)
from prismclaw.engine.hooks import (
    HookDispatcher,
    HookEvent,
    HookHandler,
    HookResult,
)
from prismclaw.engine.toolkit.registry.governance import normalize_registry_tool_names
from prismclaw.engine.toolkit.registry.registry import ToolRegistry
from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext
from prismclaw.schema.content.content_types import ToolResult


def _ctx(scope: frozenset[str] | None) -> ToolContext:
    return ToolContext(workspace_id="w", workspace_path=Path("/tmp"), tool_scope=scope)


class _PluginTool:
    """最小 Tool Protocol 实现；含 server_config 以便名字可带冒号。"""

    def __init__(self, name: str, owner: str | None) -> None:
        self.name = name
        self.plugin_name = owner
        self.description = "d"
        self.parameters = {"type": "object", "properties": {}}
        self.side_effect = False
        self.is_concurrency_safe = True
        self.tool_class = "read"
        self.effect_class = "pure_read"
        if ":" in name:
            self.server_config = object()

    async def execute(self, args, context) -> ToolResult:
        return ToolResult(tool_call_id="", content=[], is_error=False)


@pytest.fixture()
def registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(_PluginTool("legal:search", "legal"))
    reg.register(_PluginTool("fin:report", "fin"))
    reg.register(_PluginTool("core_read", None))
    return reg


def _calls(*names: str) -> list[dict]:
    return [
        {"id": str(i + 1), "function": {"name": n, "arguments": "{}"}} for i, n in enumerate(names)
    ]


def _run(registry: ToolRegistry, calls, scope):
    return asyncio.run(execute_preflighted_tool_calls(registry, calls, _ctx(scope)))


# ── 闸门 ① 工具 ────────────────────────────────────────────────────────


def test_inactive_plugin_tool_denied(registry):
    [res] = _run(registry, _calls("legal__search"), frozenset({"fin"}))
    assert res.is_error
    assert "tool_scope_denied:legal" in res.content[0].text


def test_active_plugin_tool_allowed(registry):
    [res] = _run(registry, _calls("legal__search"), frozenset({"legal"}))
    assert not res.is_error


def test_core_tool_always_allowed(registry):
    [res] = _run(registry, _calls("core_read"), frozenset())
    assert not res.is_error


def test_none_scope_disables_gating(registry):
    """None = 无插件上下文，插件工具也放行。"""
    [res] = _run(registry, _calls("legal__search"), None)
    assert not res.is_error


def test_mixed_batch_denies_only_out_of_scope(registry):
    legal, core = _run(registry, _calls("legal__search", "core_read"), frozenset())
    assert legal.is_error and "tool_scope_denied:legal" in legal.content[0].text
    assert not core.is_error


# ── allowed_tools 越权收敛（R1）─────────────────────────────────────────


def test_allowed_tools_cannot_unlock_other_plugin(registry):
    kept = normalize_registry_tool_names(registry, ["legal:search"], tool_scope=frozenset({"fin"}))
    assert kept == set()


def test_allowed_tools_keeps_in_scope(registry):
    kept = normalize_registry_tool_names(
        registry, ["legal:search"], tool_scope=frozenset({"legal"})
    )
    assert kept == {"legal:search"}


def test_allowed_tools_unfiltered_without_scope(registry):
    kept = normalize_registry_tool_names(registry, ["legal:search"], tool_scope=None)
    assert kept == {"legal:search"}


# ── 闸门 ③ Hook ────────────────────────────────────────────────────────


class _Counter(HookHandler):
    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, context) -> HookResult:
        self.calls += 1
        return HookResult.success()


def _dispatch(dispatcher, scope):
    asyncio.run(dispatcher.dispatch(HookEvent.POST_TOOL, {}, active_plugins=scope))


def test_inactive_plugin_hook_skipped():
    d = HookDispatcher()
    plugin_hook, core_hook = _Counter(), _Counter()
    d.register(HookEvent.POST_TOOL, plugin_hook, plugin_name="legal")
    d.register(HookEvent.POST_TOOL, core_hook)

    _dispatch(d, frozenset())
    assert plugin_hook.calls == 0
    assert core_hook.calls == 1

    _dispatch(d, frozenset({"legal"}))
    assert plugin_hook.calls == 1


def test_hook_runs_when_scope_is_none():
    d = HookDispatcher()
    hook = _Counter()
    d.register(HookEvent.POST_TOOL, hook, plugin_name="legal")
    _dispatch(d, None)
    assert hook.calls == 1


# ── 闸门 ④ 子代理目录 ──────────────────────────────────────────────────


def _agent(name: str, plugin: str | None):
    return SimpleNamespace(
        name=name,
        description="x",
        marketplace_name=None,
        marketplace_description=None,
        plugin_name=plugin,
    )


@pytest.fixture()
def discovery() -> SubagentDiscoveryRuntime:
    catalog = SimpleNamespace(
        list_agent_entries=lambda: [
            _agent("a", "legal"),
            _agent("b", "fin"),
            _agent("core", None),
        ]
    )
    return SubagentDiscoveryRuntime(capability_catalog=catalog)


def _plugin_names(discovery, scope) -> set[str]:
    return {agent.name for agent, is_core in discovery._candidates(scope) if not is_core}


def test_plugin_agents_filtered_by_scope(discovery):
    assert _plugin_names(discovery, frozenset({"legal"})) == {"a"}


def test_plugin_agents_empty_scope(discovery):
    assert _plugin_names(discovery, frozenset()) == set()


def test_plugin_agents_unfiltered_without_scope(discovery):
    assert _plugin_names(discovery, None) == {"a", "b"}


def test_core_agent_never_filtered_by_scope(discovery):
    """核心子代理不受插件作用域约束，始终是检索兜底（D7）。"""
    core_names = {agent.name for agent, is_core in discovery._candidates(frozenset()) if is_core}
    assert core_names == {"core"}
