"""专家包作用域门控：两个「代码就位但此前无单测」的分支。

1. 闸门 ② — ActivateSkillTool 的技能归属校验（skill_scope_denied）
2. 闸门 ④ 二次校验 — SubAgentTool 派发前的 agent 归属校验（agent_scope_denied）

两项在参数校验阶段即返回，不需要真实执行器/运行时。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from prismclaw.engine.experts.codes import ExpertErrorCode
from prismclaw.engine.experts.scope import UNRESTRICTED_SURFACE, NoExpertScope, RejectedScope
from prismclaw.engine.subagent.runtime.collaboration import (
    SubagentCollaborationError,
    SubagentCollaborationService,
)
from prismclaw.engine.subagent.tool import SubAgentTool
from prismclaw.engine.toolkit.builtin.skill_activate import ActivateSkillTool
from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext
from prismclaw.schema.agent.domains import InvocationPolicy
from prismclaw.schema.config.config import SubAgentConfig


def _ctx(scope: frozenset[str] | None) -> ToolContext:
    session_scope = (
        None
        if scope is None
        else NoExpertScope(
            tool_surface=UNRESTRICTED_SURFACE,
            tool_owners=scope,
            member=False,
        )
    )
    return ToolContext(workspace_id="w", workspace_path=Path("/tmp"), session_scope=session_scope)


# ══════════════════════════════════════════════════════════════════════
# 闸门 ② — 技能归属校验
# ══════════════════════════════════════════════════════════════════════


class _Catalog:
    """只实现门控所需的 resolve_skill_reference 契约。"""

    def __init__(self, owner: str | None, kind: str = "resolved") -> None:
        self._owner = owner
        self._kind = kind

    def resolve_skill_reference(
        self, name: str, *, expert_prefer: str | None = None
    ) -> SimpleNamespace:
        entry = (
            SimpleNamespace(
                name=name,
                expert_name=self._owner,
                manifest=SimpleNamespace(invocation_policy=InvocationPolicy.AUTO),
            )
            if self._kind == "resolved"
            else None
        )
        return SimpleNamespace(kind=self._kind, entry=entry)


class _FakeSkillRuntime:
    def build_skill_execution(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("should not reach runtime")


def _activate(owner: str | None, scope: frozenset[str] | None) -> object:
    tool = ActivateSkillTool(
        skill_runtime=_FakeSkillRuntime(),
        capability_catalog=_Catalog(owner),
    )
    return asyncio.run(tool.execute({"skill_name": "contract-review"}, _ctx(scope)))


def test_skill_of_inactive_expert_denied() -> None:
    res = _activate("legal", frozenset({"fin"}))
    assert res.is_error
    assert "skill_scope_denied:legal" in res.content[0].text


def test_skill_of_active_expert_passes_gate() -> None:
    """放行后进入下游；下游失败不是 scope 拒绝（不得出现 skill_scope_denied）。"""
    res = _activate("legal", frozenset({"legal"}))
    assert "skill_scope_denied" not in res.content[0].text


def test_core_skill_never_denied() -> None:
    """expert_name=None 的核心技能恒放行，即使 scope 为空集。"""
    res = _activate(None, frozenset())
    assert "skill_scope_denied" not in res.content[0].text


def test_skill_gate_disabled_when_scope_is_none() -> None:
    """None = 无专家包上下文，不做作用域限制。"""
    res = _activate("legal", None)
    assert "skill_scope_denied" not in res.content[0].text


def test_unresolved_skill_falls_through_to_downstream() -> None:
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

    def model_copy(self, update: dict[str, object] | None = None) -> object:
        return self


def _agent(name: str, expert: str | None) -> SimpleNamespace:
    return SimpleNamespace(name=name, manifest=_Manifest(), expert_name=expert)


class _AgentCatalog:
    def __init__(self, entries: list) -> None:
        self._entries = entries

    def get_agent_entry(self, name: str) -> object | None:
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


def _spawn(tool: SubAgentTool, agent_name: str, scope: frozenset[str] | None) -> object:
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


def test_agent_of_inactive_expert_denied_at_dispatch() -> None:
    tool = _subagent_tool([_agent("researcher", "legal")])
    res = _spawn(tool, "researcher", frozenset({"fin"}))
    assert res.is_error
    assert "agent_scope_denied:legal" in res.content[0].text


def test_agent_denial_blocks_before_execution() -> None:
    """被拒时不得产生任何执行结果（错误列表短路返回）。"""
    tool = _subagent_tool([_agent("researcher", "legal")])
    res = _spawn(tool, "researcher", frozenset())
    assert res.is_error
    assert "参数校验失败" in res.content[0].text


def test_core_agent_never_denied() -> None:
    """核心子代理（expert_name=None）即使 scope 为空集也不被 scope 拒绝。

    放行后会进入执行阶段（此处因 stub executor 缺 model 而失败），
    断言点在「错误信息不是 agent_scope_denied」。
    """
    tool = _subagent_tool([_agent("core-agent", None)])
    res = _spawn(tool, "core-agent", frozenset())
    assert "agent_scope_denied" not in (res.content[0].text if res.content else "")


def test_agent_gate_disabled_when_scope_is_none() -> None:
    tool = _subagent_tool([_agent("researcher", "legal")])
    res = _spawn(tool, "researcher", None)
    assert "agent_scope_denied" not in (res.content[0].text if res.content else "")


def test_unknown_agent_still_reports_not_found() -> None:
    """未知 agent 走原有错误路径，不应被误判为 agent_scope_denied。"""
    tool = _subagent_tool([_agent("researcher", "legal")])
    res = _spawn(tool, "nobody", frozenset({"legal"}))
    assert res.is_error
    assert "未找到子代理" in res.content[0].text
    assert "agent_scope_denied" not in res.content[0].text


# ══════════════════════════════════════════════════════════════════════
# 闸门 ⑤ — 父作用域被拒时禁止派生
# ══════════════════════════════════════════════════════════════════════


def _rejected_ctx(code: ExpertErrorCode) -> ToolContext:
    return ToolContext(
        workspace_id="w",
        workspace_path=Path("/tmp"),
        session_scope=RejectedScope(
            code=code,
            expert_id="legal",
            reason="expert is disabled",
        ),
    )


def test_subagent_refuses_when_parent_scope_rejected() -> None:
    """父会话专家被拒时，连核心子代理也不得派生。"""
    tool = _subagent_tool([_agent("core-agent", None)])
    res = asyncio.run(
        tool.execute(
            {"_call_id": "c1", "agents": [{"agent_name": "core-agent", "task": "t"}]},
            _rejected_ctx(ExpertErrorCode.MODULE_DISABLED),
        )
    )
    assert res.is_error
    assert "MODULE_DISABLED" in res.content[0].text


def test_collaboration_refuses_run_for_rejected_parent_scope() -> None:
    """协作/后台路径的 spawn 同样拒绝：否则禁用后仍可经 subagent_control 启动。"""
    from tests.engine.session_store import FakeRedisSessionStore

    service = SubagentCollaborationService(session_store=FakeRedisSessionStore())
    launch = SimpleNamespace(
        manifest=SimpleNamespace(name="core-agent"),
        parent_ctx=_rejected_ctx(ExpertErrorCode.MODULE_DISABLED),
    )

    with pytest.raises(SubagentCollaborationError, match="MODULE_DISABLED"):
        asyncio.run(
            service.spawn(launch, task="t", lifecycle="one_shot", request_id="r1")
        )
