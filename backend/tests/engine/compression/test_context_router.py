"""批 6：context_router 硬编码配额删除 契约测试。

锁定：
1. intent_bias 值经配置驱动输出。
2. intent_bias 缺键 → 0 增量（不再回退旧硬编码配额表）。
"""

from __future__ import annotations

from types import SimpleNamespace

from prismclaw.engine.compression.context_router import DynamicContextRouter
from prismclaw.schema.content.context import TaskProfile


def _full_intent_bias() -> dict[str, int]:
    return {
        "prefers_tooling_l3": 3,
        "prefers_tooling_l2": -1,
        "prefers_tooling_archive": -1,
        "requires_grounding_l2": 3,
        "requires_grounding_archive": 2,
        "requires_grounding_l3": -1,
    }


def _settings(intent_bias: dict[str, int]) -> SimpleNamespace:
    return SimpleNamespace(
        search_l2_quota=3,
        search_l3_quota=2,
        archive_max_results=5,
        intent_bias=intent_bias,
    )


def test_intent_bias_value_influences_output(monkeypatch):
    bias = _full_intent_bias()
    bias["prefers_tooling_l3"] = 10
    settings = _settings(bias)

    l2, l3, archive = DynamicContextRouter.allocate_quotas(
        task_profile=TaskProfile(prefers_tooling=True),
        settings=settings,
    )
    assert l3 == 2 + 10  # 基础 2 + 配置偏置 10
    assert l2 == 3 - 1
    # prefers_tooling 未声明 requires_grounding → 归档按 DD-9 收窄到 ≤1，
    # 此时 archive 偏置的加项不再生效（min 收口）。
    assert archive == 1


def test_missing_intent_bias_key_gives_zero_increment(monkeypatch):
    settings = _settings({})  # 全部键缺失
    l2, l3, archive = DynamicContextRouter.allocate_quotas(
        task_profile=TaskProfile(prefers_tooling=True, requires_grounding=True),
        settings=settings,
    )
    # 缺键 → 0 增量；基础配额原样保留
    assert (l2, l3, archive) == (3, 2, 5)


def test_missing_bias_keys_zero_increment():
    """缺键 → 0 增量，基础配额原样保留（意图维度是唯一配额来源）。"""
    settings = _settings({})
    l2, l3, archive = DynamicContextRouter.allocate_quotas(
        task_profile=TaskProfile(),
        settings=settings,
    )
    assert l2 == 3
    assert l3 == 2
    assert archive == 1


def test_archive_narrowed_when_grounding_not_required():
    """T10：不需要取证时归档收窄到 ≤1，避免无谓翻阅归档（DD-9）。"""
    bias = _full_intent_bias()
    settings = _settings(bias)

    _, _, archive = DynamicContextRouter.allocate_quotas(
        task_profile=TaskProfile(requires_grounding=False),
        settings=settings,
    )
    assert archive <= 1


def test_archive_open_when_grounding_required():
    """T10：需要取证时归档放开，与轮次无关（DD-9）。"""
    bias = _full_intent_bias()
    settings = _settings(bias)

    _, _, archive = DynamicContextRouter.allocate_quotas(
        task_profile=TaskProfile(requires_grounding=True),
        settings=settings,
    )
    assert archive == 5 + 2
