"""壳缓存键与容量（SDD-ARCH-001 步 4 / T8 / G7）。

锁三件事：
- 键由 workspace 事实与本轮可见集组成，不渗入决策分量；
- 键覆盖 ``<tools>`` 段的直接输入（本轮可见集），可见集变化必须换新壳；
- 条目数受 LRU 上限约束，且命中会续期。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

from prismclaw.engine.execution.loop.deps import AgentRunnerDeps
from prismclaw.engine.execution.query._static_context_types import (
    ShellCacheKey,
    StaticTurnShellCache,
    StaticTurnShellSnapshot,
    _shell_cache_key,
)
from prismclaw.schema.agent.types import RunRequest

_VISIBLE = {"read", "tool_search"}


def _snapshot() -> StaticTurnShellSnapshot:
    return cast(StaticTurnShellSnapshot, object())


def _request() -> SimpleNamespace:
    return SimpleNamespace(session_id="sess-1", workspace_id="ws-1")


def _deps(generation: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        expert=SimpleNamespace(lifecycle_manager=SimpleNamespace(generation=generation))
    )


def _key(
    *,
    generation: int = 0,
    discovered: set[str] | None = None,
    depth: int = 0,
    visible: set[str] | None = None,
    expert_id: str = "",
    expert_persona_digest: str = "",
) -> ShellCacheKey:
    return _shell_cache_key(
        request=cast(RunRequest, _request()),
        tool_workspace_path="/tmp/ws",
        effective_model="m",
        deps=cast(AgentRunnerDeps, _deps(generation)),
        skill_invocation=None,
        discovered_tools=discovered,
        subagent_depth=depth,
        visible_tool_names=set(_VISIBLE if visible is None else visible),
        expert_id=expert_id,
        expert_persona_digest=expert_persona_digest,
    )


def test_cache_key_is_thirteen_scalar_facts() -> None:
    key = _key()
    assert len(key) == 13
    # 第 7 位是 (skill_name, *allowed_tools)，无技能激活时为单元素空串元组
    assert key[6] == ("",)


def test_cache_key_varies_with_facts() -> None:
    base = _key()
    assert _key(generation=1) != base
    assert _key(discovered={"bash"}) != base
    assert _key(depth=1) != base


def test_cache_key_varies_with_visible_tools() -> None:
    """``<tools>`` 段按可见集渲染，可见集变化必须取值不同（D14 回归）。"""
    base = _key()
    assert _key(visible={*_VISIBLE, "legal_lookup"}) != base
    assert _key(visible={"read"}) != base
    # 集合元素顺序不影响结果
    assert _key(visible={"tool_search", "read"}) == base


def test_cache_key_varies_with_expert_identity_and_persona() -> None:
    base = _key()
    assert _key(expert_id="writer") != base
    assert _key(expert_persona_digest="new-persona") != base


def test_lru_evicts_oldest_beyond_capacity() -> None:
    cache = StaticTurnShellCache(max_entries=3)
    keys = [_key(discovered={name}) for name in ("a", "b", "c", "d")]
    for key in keys:
        cache.put(key, _snapshot())

    assert len(cache.entries) == 3
    assert keys[0] not in cache.entries
    assert keys[3] in cache.entries


def test_lru_get_refreshes_recency() -> None:
    cache = StaticTurnShellCache(max_entries=2)
    k1, k2, k3 = (_key(discovered={name}) for name in ("a", "b", "c"))
    cache.put(k1, _snapshot())
    cache.put(k2, _snapshot())

    assert cache.get(k1) is not None  # k1 续期，k2 成为最旧
    cache.put(k3, _snapshot())

    assert k1 in cache.entries
    assert k2 not in cache.entries
    assert k3 in cache.entries


def test_put_existing_key_does_not_grow() -> None:
    cache = StaticTurnShellCache(max_entries=2)
    k1 = _key(discovered={"a"})
    cache.put(k1, _snapshot())
    cache.put(k1, _snapshot())
    assert len(cache.entries) == 1
