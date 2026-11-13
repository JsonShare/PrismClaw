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
from prismclaw.engine.security.gate.types import AllowedToolsPolicy  # noqa: E402


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
        allowed_tools_policy=AllowedToolsPolicy(
            allow_all=False, allowed_names=("read", "goal")
        ),
    )
    assert set(resolved.visible_tools_for_llm) == {"read", "goal"}


def test_core_set_covers_universal_tools():
    from prismclaw.engine.toolkit.registry.governance import _UNIVERSAL_TOOLS

    assert _UNIVERSAL_TOOLS <= _CORE_VISIBLE_TOOLS


def test_core_set_matches_expected_baseline():
    assert _CORE_VISIBLE_TOOLS == frozenset({
        "read", "write", "edit", "bash", "ls", "grep", "glob",
        "todo_write", "ask_user_question",
        "tool_search", "skill_search", "subagent_search", "activate_skill",
        "present_files",
    })
