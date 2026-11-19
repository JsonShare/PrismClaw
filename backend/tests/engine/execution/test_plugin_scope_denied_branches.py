"""插件作用域门控：三个「代码就位但此前无单测」的分支。

1. 闸门 ② — ActivateSkillTool 的技能归属校验（skill_scope_denied）
2. 闸门 ④ 二次校验 — SubAgentTool 派发前的 agent 归属校验（agent_scope_denied）
3. §3.9 架构降级 — 多插件只注入首个 persona，并记 plugin_persona_degraded

前两项在参数校验阶段即返回，不需要真实执行器/运行时；
第三项用真实 RunSnapshot，直接断言注入内容与日志。
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace

from prismclaw.engine.subagent.tool import SubAgentTool
from prismclaw.engine.toolkit.builtin.skill_activate import ActivateSkillTool
from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext
from prismclaw.schema.config.config import SubAgentConfig


def _ctx(scope: frozenset[str] | None) -> ToolContext:
    return ToolContext(
        workspace_id="w", workspace_path=Path("/tmp"), tool_scope=scope
    )


# ══════════════════════════════════════════════════════════════════════
# 闸门 ② — 技能归属校验
# ══════════════════════════════════════════════════════════════════════


class _Catalog:
    """只实现门控所需的 resolve_skill_reference 契约。"""

    def __init__(self, owner: str | None, kind: str = "resolved") -> None:
        self._owner = owner
        self._kind = kind

    def resolve_skill_reference(self, name: str, *, plugin_prefer=None):
        entry = (
            SimpleNamespace(name=name, plugin_name=self._owner)
            if self._kind == "resolved"
            else None
        )
        return SimpleNamespace(kind=self._kind, entry=entry)


class _FakeSkillRuntime:
    def build_skill_execution(self, *args, **kwargs):
        raise AssertionError("should not reach runtime")


def _activate(owner: str | None, scope: frozenset[str] | None):
    tool = ActivateSkillTool(
        skill_runtime=_FakeSkillRuntime(),
        capability_catalog=_Catalog(owner),
    )
    return asyncio.run(
        tool.execute({"skill_name": "contract-review"}, _ctx(scope))
    )


def test_skill_of_inactive_plugin_denied():
    res = _activate("legal", frozenset({"fin"}))
    assert res.is_error
    assert "skill_scope_denied:legal" in res.content[0].text


def test_skill_of_active_plugin_passes_gate():
    """放行后进入下游；下游失败不是 scope 拒绝（不得出现 skill_scope_denied）。"""
    res = _activate("legal", frozenset({"legal"}))
    assert "skill_scope_denied" not in res.content[0].text


def test_core_skill_never_denied():
    """plugin_name=None 的核心技能恒放行，即使 scope 为空集。"""
    res = _activate(None, frozenset())
    assert "skill_scope_denied" not in res.content[0].text


def test_skill_gate_disabled_when_scope_is_none():
    """None = 无插件上下文，不做作用域限制。"""
    res = _activate("legal", None)
    assert "skill_scope_denied" not in res.content[0].text


def test_unresolved_skill_falls_through_to_downstream():
    """解析不到归属时不得误杀，交给下游维持原有错误路径。"""
    tool = ActivateSkillTool(
        skill_runtime=_FakeSkillRuntime(),
        capability_catalog=_Catalog(None, kind="not_found"),
    )
    res = asyncio.run(tool.execute({"skill_name": "unknown"}, _ctx(frozenset())))
    assert "skill_scope_denied" not in res.content[0].text


# ══════════════════════════════════════════════════════════════════════
# 闸门 ④ 二次校验 — 派发前 agent 归属校验
# ══════════════════════════════════════════════════════════════════════


class _Manifest:
    """最小 manifest stub：只需满足放行路径上的属性访问。"""

    name = "stub"
    model = "stub-model"
    context_mode = "isolated"
    can_handoff_to = ()

    def model_copy(self, update=None):
        return self


def _agent(name: str, plugin: str | None):
    return SimpleNamespace(name=name, manifest=_Manifest(), plugin_name=plugin)


class _AgentCatalog:
    def __init__(self, entries: list) -> None:
        self._entries = entries

    def get_agent_entry(self, name: str):
        return next((e for e in self._entries if e.name == name), None)

    def list_agent_entries(self) -> list:
        return self._entries

    def count_agent_entries(self) -> int:
        return len(self._entries)


def _subagent_tool(entries: list) -> SubAgentTool:
    # executor 为 stub：本文件只验证「校验阶段」的门控与错误路径，
    # 放行后的执行不在测试范围内（会被 stub 的缺失能力挡住，不影响断言）。
    return SubAgentTool(
        executor=SimpleNamespace(),
        registry=SimpleNamespace(),
        settings=SubAgentConfig(),
        capability_catalog=_AgentCatalog(entries),
    )


def _spawn(tool: SubAgentTool, agent_name: str, scope: frozenset[str] | None):
    """派发；执行阶段异常视为「未被 scope 拒绝」，统一转成文本便于断言。"""
    try:
        return asyncio.run(
            tool.execute(
                {"_call_id": "c1", "agents": [{"agent_name": agent_name, "task": "t"}]},
                _ctx(scope),
            )
        )
    except Exception as exc:  # noqa: BLE001 - 放行后的执行失败不是被测目标
        return SimpleNamespace(
            is_error=True,
            content=[SimpleNamespace(text=f"execution-stage: {exc!r}")],
        )


def test_agent_of_inactive_plugin_denied_at_dispatch():
    tool = _subagent_tool([_agent("researcher", "legal")])
    res = _spawn(tool, "researcher", frozenset({"fin"}))
    assert res.is_error
    assert "agent_scope_denied:legal" in res.content[0].text


def test_agent_denial_blocks_before_execution():
    """被拒时不得产生任何执行结果（错误列表短路返回）。"""
    tool = _subagent_tool([_agent("researcher", "legal")])
    res = _spawn(tool, "researcher", frozenset())
    assert res.is_error
    assert "参数校验失败" in res.content[0].text


def test_core_agent_never_denied():
    """核心子代理（plugin_name=None）即使 scope 为空集也不被 scope 拒绝。

    放行后会进入执行阶段（此处因 stub executor 缺 model 而失败），
    断言点在「错误信息不是 agent_scope_denied」。
    """
    tool = _subagent_tool([_agent("core-agent", None)])
    res = _spawn(tool, "core-agent", frozenset())
    assert "agent_scope_denied" not in (res.content[0].text if res.content else "")


def test_agent_gate_disabled_when_scope_is_none():
    tool = _subagent_tool([_agent("researcher", "legal")])
    res = _spawn(tool, "researcher", None)
    assert "agent_scope_denied" not in (res.content[0].text if res.content else "")


def test_unknown_agent_still_reports_not_found():
    """未知 agent 走原有错误路径，不应被误判为 agent_scope_denied。"""
    tool = _subagent_tool([_agent("researcher", "legal")])
    res = _spawn(tool, "nobody", frozenset({"legal"}))
    assert res.is_error
    assert "未找到子代理" in res.content[0].text
    assert "agent_scope_denied" not in res.content[0].text


# ══════════════════════════════════════════════════════════════════════
# §3.9 多插件降级 — 只取首个 persona + 降级日志
# ══════════════════════════════════════════════════════════════════════


class _MultiPluginRuntime:
    """所有插件都有 instructions（用于验证存在性判定与 persona 注入范围）。"""

    def __init__(self) -> None:
        self._capability_catalog = SimpleNamespace(list_skill_entries=lambda: [])

    def get_plugin_instructions(self, plugin_name: str) -> str:
        # 真实实现返回 str（空串表示无），此处两插件都有 persona
        return f"PERSONA::{plugin_name}"


def _run_activation(chips: list[str], caplog):
    from prismclaw.engine.execution.loop.preparer import RunPreparer
    from prismclaw.schema.agent.types import RunRequest

    # 只测激活分支：绕过构造函数装配，__new__ 取得裸实例
    # （该方法只用到 self._merge_invocations，是纯静态逻辑）。
    preparer = RunPreparer.__new__(RunPreparer)
    runtime = _MultiPluginRuntime()
    deps = SimpleNamespace(skill_runtime=runtime)
    tool_ctx = _ctx(None)
    # 用真实 RunSnapshot：激活末尾会经 project_active_skill_invocation
    # 写回 system_prompt_sections，SimpleNamespace 不够。
    from prismclaw.engine.execution.checkpoint.persistence import RunSnapshot
    from prismclaw.engine.execution.checkpoint.models import RunState

    snapshot = RunSnapshot(run_state=RunState(session_id="s1"))
    injections: list[str] = []

    with caplog.at_level(logging.INFO):
        asyncio.run(
            preparer._activate_designated_plugins(
                RunRequest(
                    session_id="s1",
                    workspace_id="w",
                    agent_id="a",
                    user_message="hi",
                    sandbox_policy=None,
                    designated_plugins=chips,
                ),
                deps,
                snapshot,
                tool_ctx,
                None,
                None,
                injections,
            )
        )
    return runtime, tool_ctx, injections

def test_multi_plugin_only_first_persona_injected(caplog):
    """同时 @ 两个插件：只有第一个的 persona 进入注入队列；两者都解锁能力。"""
    _runtime, tool_ctx, injections = _run_activation(["legal", "fin"], caplog)
    assert tool_ctx.tool_scope == frozenset({"legal", "fin"})
    assert any("PERSONA::legal" in i for i in injections)
    assert not any("PERSONA::fin" in i for i in injections)


def test_multi_plugin_logs_degraded(caplog):
    _run_activation(["legal", "fin"], caplog)
    degraded = [
        r for r in caplog.records
        if "plugin_persona_degraded" in r.getMessage()
    ]
    assert degraded, "缺少 plugin_persona_degraded 日志"
    msg = degraded[0].getMessage()
    assert "primary=legal" in msg and "fin" in msg


def test_single_plugin_no_degraded_log(caplog):
    _runtime, tool_ctx, injections = _run_activation(["legal"], caplog)
    assert tool_ctx.tool_scope == frozenset({"legal"})
    assert any("PERSONA::legal" in i for i in injections)
    assert not [
        r for r in caplog.records
        if "plugin_persona_degraded" in r.getMessage()
    ]
