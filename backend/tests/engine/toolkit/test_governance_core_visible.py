"""DeferredToolReducer 的核心可见集（tools.core_visible_only flag）语义。

锁定 pi 式渐进披露契约：
- flag 关闭：非核心、非 deferred 工具照常可见（默认路径零变化）；
- flag 开启：核心集外工具不可见不可直调，但仍 searchable（可发现）；
- 被发现（discovered_tools）的工具下一轮恢复可见可调；
- 显式 allowed_tools 白名单优先于核心集隐藏（subagent 分派不受影响）。
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest  # noqa: E402

from prismclaw.engine.observability.feature_flags import (  # noqa: E402
    FLAG_CORE_VISIBLE_TOOLS,
    clear_static_flags,
    set_static_flag,
)
from prismclaw.engine.security.gate.types import AllowedToolsPolicy  # noqa: E402
from prismclaw.engine.toolkit.registry.governance import (  # noqa: E402
    _CORE_VISIBLE_TOOLS,
    GovernanceInput,
    ToolGovernance,
)
from prismclaw.engine.toolkit.registry.specs import (  # noqa: E402
    ToolExecutionTraits,
    ToolPermissionTraits,
    ToolSpec,
)


def _spec(name: str, *, deferred: bool = False) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=f"{name} desc",
        input_schema={"type": "object", "properties": {}},
        execution_traits=ToolExecutionTraits(requires_discovery=deferred),
        permission_traits=ToolPermissionTraits(),
        executor_kind="local",
    )


@pytest.fixture(autouse=True)
def _clean_flags():
    clear_static_flags()
    yield
    clear_static_flags()


def _resolve(catalog, **kwargs) -> object:
    return ToolGovernance().resolve(GovernanceInput(catalog=catalog, **kwargs))


def test_flag_off_keeps_non_core_tools_visible():
    catalog = [_spec("read"), _spec("goal"), _spec("memory")]
    resolved = _resolve(catalog)
    assert set(resolved.visible_tools_for_llm) == {"read", "goal", "memory"}


def test_flag_on_hides_non_core_tools_but_keeps_searchable():
    set_static_flag(FLAG_CORE_VISIBLE_TOOLS, True)
    catalog = [_spec("read"), _spec("goal"), _spec("present_files")]
    resolved = _resolve(catalog)

    assert set(resolved.visible_tools_for_llm) == {"read", "present_files"}
    # 不可见但可搜索：deferred 的语义是「先发现、后调用」
    assert "goal" in resolved.searchable_tools
    assert "goal" in resolved.denied_tools

    hidden = [e for e in resolved.decision_trace if e.tool_name == "goal"]
    assert hidden and hidden[0].reason == "tool_outside_core_visible_set"


def test_flag_on_discovered_tool_unlocked_next_turn():
    set_static_flag(FLAG_CORE_VISIBLE_TOOLS, True)
    catalog = [_spec("read"), _spec("goal")]
    resolved = _resolve(catalog, discovered_tools=frozenset({"goal"}))
    assert set(resolved.visible_tools_for_llm) == {"read", "goal"}


def test_flag_on_explicit_allowlist_overrides_core_hiding():
    """subagent 分派的显式 allowed_tools 不受核心集隐藏影响。"""
    set_static_flag(FLAG_CORE_VISIBLE_TOOLS, True)
    catalog = [_spec("read"), _spec("goal")]
    resolved = _resolve(
        catalog,
        allowed_tools_policy=AllowedToolsPolicy(allow_all=False, allowed_names=("read", "goal")),
    )
    assert set(resolved.visible_tools_for_llm) == {"read", "goal"}


def test_core_set_covers_universal_tools():
    from prismclaw.engine.toolkit.registry.governance import _UNIVERSAL_TOOLS

    assert _UNIVERSAL_TOOLS <= _CORE_VISIBLE_TOOLS


def test_skill_allowlist_does_not_hide_universal_tools():
    """skill 的 allowed_tools 白名单不能隐藏 todo_write 等通用工具。"""
    resolved = _resolve(
        [_spec("read"), _spec("todo_write")],
        allowed_tools_policy=AllowedToolsPolicy(allow_all=False, allowed_names=("read",)),
    )
    assert set(resolved.visible_tools_for_llm) == {"read", "todo_write"}
    assert "todo_write" not in resolved.denied_tools


def test_server_read_only_hides_write_tools_but_keeps_read_only_capable():
    """server_read_only 的服务器：写类工具不可见；显式 read_only_allowed 的工具保留。"""
    write_tool = _spec("mcp_write").model_copy(
        update={
            "permission_traits": ToolPermissionTraits(
                tool_class="write",
                server_read_only=True,
                read_only_allowed=False,
            )
        }
    )
    read_tool = _spec("mcp_read").model_copy(
        update={
            "permission_traits": ToolPermissionTraits(
                tool_class="write",
                server_read_only=True,
                read_only_allowed=True,
            )
        }
    )
    resolved = _resolve([write_tool, read_tool])

    assert "mcp_write" not in resolved.visible_tools_for_llm
    assert "mcp_write" in resolved.denied_tools
    assert "mcp_read" in resolved.visible_tools_for_llm

    trace = [e for e in resolved.decision_trace if e.tool_name == "mcp_write"]
    assert trace and trace[0].reducer == "ServerReadOnlyReducer"
    assert trace[0].reason == "tool_server_read_only_blocks_write"


def test_core_set_matches_expected_baseline():
    assert (
        frozenset(
            {
                "read",
                "write",
                "edit",
                "bash",
                "ls",
                "grep",
                "glob",
                "todo_write",
                "ask_user_question",
                "tool_search",
                "skill_search",
                "subagent_search",
                "activate_skill",
                "present_files",
                "sub_agent",
            }
        )
        == _CORE_VISIBLE_TOOLS
    )


def test_sub_agent_is_universal_and_core():
    """D6 两条路径：委派既不能被技能白名单吞掉，也不能被核心集隐藏。"""
    from prismclaw.engine.toolkit.registry.governance import _UNIVERSAL_TOOLS

    assert "sub_agent" in _UNIVERSAL_TOOLS
    assert "sub_agent" in _CORE_VISIBLE_TOOLS


def test_skill_allowlist_keeps_sub_agent():
    """D6 路径一：技能声明 allowed_tools 后，<delegation> 依赖的 sub_agent 仍在。"""
    resolved = _resolve(
        [_spec("read"), _spec("sub_agent")],
        allowed_tools_policy=AllowedToolsPolicy(allow_all=False, allowed_names=("read",)),
    )
    assert "sub_agent" in resolved.visible_tools_for_llm
    assert "sub_agent" not in resolved.denied_tools


def test_core_visible_flag_keeps_sub_agent():
    """D6 路径二：core_visible_only 开启时委派入口不被视作 deferred。"""
    set_static_flag(FLAG_CORE_VISIBLE_TOOLS, True)
    resolved = _resolve([_spec("read"), _spec("sub_agent"), _spec("goal")])
    assert "sub_agent" in resolved.visible_tools_for_llm
    assert "goal" not in resolved.visible_tools_for_llm
