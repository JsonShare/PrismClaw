"""Phase 1 行为验证：Run 级记忆组合缓存的检索调用次数。

验证机制本身而非性能数字：注入缓存后，输入不变的第二次
assemble 不得再次触发记忆检索；输入（anchor digest）变化则必须
重算。检索调用用假 retriever 计数，其余重依赖以最小 patch 隔离。
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from prismclaw.engine.compression.assemble_coordinator import AssembleCoordinator
from prismclaw.engine.compression.memory_assembly_cache import MemoryCoreCache
from prismclaw.engine.compression.state_store import SessionStateStore
from prismclaw.engine.compression.task_profile_inference import TaskProfileInference
from prismclaw.schema.content.evidence import EvidenceStrategy


class _FakeRetriever:
    def __init__(self) -> None:
        self.retrieve_calls = 0
        self.last_kwargs: dict = {}
        self.interactive_retrieval_budget_s = 3.0

    def build_evidence_strategy(self, **kwargs):
        return EvidenceStrategy(strategy_id="fake", session_id="s", strategy="none")

    async def retrieve_evidence(self, **kwargs):
        self.retrieve_calls += 1
        self.last_kwargs = kwargs
        return [], [], []


class _FakeFormatter:
    def format_blocks_from_packets(self, packets, *, graph):
        return []


class _FakeValidatorChain:
    token_budget = None

    async def validate_and_transform(self, memory_plan, prompt):
        return prompt


class _FakePorts:
    def get_context_validator_chain(self):
        return _FakeValidatorChain()


class _FakeStateStore(SessionStateStore):
    def __init__(self) -> None:
        pass

    def get_existing_state(self, session_id):
        return None

    def build_memory_block(self, session_id):
        return None

    def build_persisted_memory_block(self, session_id, *, baseline_state=None):
        return None

    def build_context_block(self, session_id, *, baseline_state=None):
        return None

    def hydrate_state(self, session_id, state) -> None:
        pass


def _coordinator(retriever) -> AssembleCoordinator:
    coordinator = AssembleCoordinator(
        memory_retriever=retriever,
        state_store=_FakeStateStore(),
        memory_formatter=_FakeFormatter(),  # type: ignore[arg-type]
        task_profile_inference=TaskProfileInference(),
    )
    coordinator.set_ports(_FakePorts())  # type: ignore[arg-type]
    return coordinator


@pytest.mark.asyncio
async def test_cache_skips_retrieval_when_inputs_unchanged():
    retriever = _FakeRetriever()
    coordinator = _coordinator(retriever)
    cache = MemoryCoreCache(label="test")
    coordinator.set_memory_core_cache(cache)

    with (
        patch.object(
            coordinator,
            "_ensure_session_state_loaded",
            return_value=({}, None, None),
        ),
        patch(
            "prismclaw.engine.compression.assemble_coordinator.build_context_graph",
            return_value=None,
        ),
    ):
        await coordinator.assemble(session_id="s", messages=[], prompt="p")
        await coordinator.assemble(session_id="s", messages=[], prompt="p")
    # 两次同输入：检索只发生一次
    assert retriever.retrieve_calls == 1
    hits, misses = cache.stats
    assert hits == 1 and misses == 1


@pytest.mark.asyncio
async def test_prompt_change_invalidates_cache():
    retriever = _FakeRetriever()
    coordinator = _coordinator(retriever)
    coordinator.set_memory_core_cache(MemoryCoreCache(label="test"))

    with (
        patch.object(
            coordinator,
            "_ensure_session_state_loaded",
            return_value=({}, None, None),
        ),
        patch(
            "prismclaw.engine.compression.assemble_coordinator.build_context_graph",
            return_value=None,
        ),
    ):
        await coordinator.assemble(session_id="s", messages=[], prompt="p1")
        await coordinator.assemble(session_id="s", messages=[], prompt="p2")
    # 任务变化 → 键变 → 重算
    assert retriever.retrieve_calls == 2


@pytest.mark.asyncio
async def test_no_cache_matches_legacy_every_call():
    retriever = _FakeRetriever()
    coordinator = _coordinator(retriever)  # 不注入缓存 = 旧路径

    with (
        patch.object(
            coordinator,
            "_ensure_session_state_loaded",
            return_value=({}, None, None),
        ),
        patch(
            "prismclaw.engine.compression.assemble_coordinator.build_context_graph",
            return_value=None,
        ),
    ):
        await coordinator.assemble(session_id="s", messages=[], prompt="p")
        await coordinator.assemble(session_id="s", messages=[], prompt="p")
    assert retriever.retrieve_calls == 2


@pytest.mark.asyncio
async def test_interactive_mode_tightens_retrieval_and_disables_llm_selector():
    """Phase 2 记忆策略：answer 模式 → 紧收预算 + 禁用 selector LLM。"""
    from prismclaw.schema.agent.task_types import TaskSpec

    retriever = _FakeRetriever()
    coordinator = _coordinator(retriever)

    with (
        patch.object(
            coordinator,
            "_ensure_session_state_loaded",
            return_value=({}, None, None),
        ),
        patch(
            "prismclaw.engine.compression.assemble_coordinator.build_context_graph",
            return_value=None,
        ),
    ):
        await coordinator.assemble(
            session_id="s",
            messages=[],
            prompt="今天天气怎么样",
            task_spec=TaskSpec(
                task_id="t1", session_id="s", goal="今天天气怎么样？"
            ),
        )
    assert retriever.retrieve_calls == 1
    assert retriever.last_kwargs["retrieval_budget_s"] == 3.0
    assert retriever.last_kwargs["llm_selector_enabled"] is False


@pytest.mark.asyncio
async def test_create_mode_keeps_default_budget_and_llm_selector():
    from prismclaw.schema.agent.task_types import TaskSpec

    retriever = _FakeRetriever()
    coordinator = _coordinator(retriever)

    with (
        patch.object(
            coordinator,
            "_ensure_session_state_loaded",
            return_value=({}, None, None),
        ),
        patch(
            "prismclaw.engine.compression.assemble_coordinator.build_context_graph",
            return_value=None,
        ),
    ):
        await coordinator.assemble(
            session_id="s",
            messages=[],
            prompt="生成报告",
            task_spec=TaskSpec(
                task_id="t2", session_id="s", goal="产出季度报告文件"
            ),
        )
    assert retriever.retrieve_calls == 1
    assert retriever.last_kwargs["retrieval_budget_s"] is None
    assert retriever.last_kwargs["llm_selector_enabled"] is True


@pytest.mark.asyncio
async def test_message_growth_does_not_invalidate_run_cache():
    """Step 2 归位核心语义：逐轮消息增量（工具结果/assistant 消息落树）
    不再改变缓存键——记忆组合（检索/profile/预算）在 Run 内只算一次。"""
    retriever = _FakeRetriever()
    coordinator = _coordinator(retriever)
    coordinator.set_memory_core_cache(MemoryCoreCache(label="test"))

    with (
        patch.object(
            coordinator,
            "_ensure_session_state_loaded",
            return_value=({}, None, None),
        ),
        patch(
            "prismclaw.engine.compression.assemble_coordinator.build_context_graph",
            return_value=None,
        ),
    ):
        await coordinator.assemble(session_id="s", messages=[], prompt="p")
        # 第二轮：历史消息增长（模拟工具结果落树后的增量投影）
        await coordinator.assemble(
            session_id="s",
            messages=[
                {"role": "user", "content": "p"},
                {"role": "assistant", "content": "ok", "tool_calls": []},
                {"role": "tool", "content": "result", "tool_call_id": "c1"},
            ],
            prompt="p",
        )
        await coordinator.assemble(
            session_id="s",
            messages=[
                {"role": "user", "content": "p"},
                {"role": "assistant", "content": "ok2", "tool_calls": []},
            ],
            prompt="p",
        )
    # 三轮组装、消息每轮增长：检索只发生一次
    assert retriever.retrieve_calls == 1
    hits, misses = cache_stats(coordinator)
    assert misses == 1 and hits == 2


@pytest.mark.asyncio
async def test_compaction_artifact_change_invalidates_cache():
    """压缩产物变化（history_artifact_id）是 Run 级唯一失效事件。"""
    retriever = _FakeRetriever()
    coordinator = _coordinator(retriever)
    coordinator.set_memory_core_cache(MemoryCoreCache(label="test"))

    class _Artifact:
        def __init__(self, artifact_id: str) -> None:
            self.artifact_id = artifact_id

    loads = [
        ({}, None, None),  # 首轮：无压缩产物
        ({}, _Artifact("art-A"), None),  # 压缩落新产物 → artifact_id 变化
    ]
    with (
        patch.object(
            coordinator,
            "_ensure_session_state_loaded",
            side_effect=loads,
        ),
        patch(
            "prismclaw.engine.compression.assemble_coordinator.build_context_graph",
            return_value=None,
        ),
    ):
        await coordinator.assemble(session_id="s", messages=[], prompt="p")
        await coordinator.assemble(session_id="s", messages=[], prompt="p")
    # 两次键不同（artifact None → art-A）：miss 两次、检索重算
    assert retriever.retrieve_calls == 2


def cache_stats(coordinator) -> tuple[int, int]:
    return coordinator._memory_core_cache.stats


class _FakeSessionStore:
    def __init__(self) -> None:
        self.load_calls = 0

    async def load(self, session_id):
        self.load_calls += 1
        return None


@pytest.mark.asyncio
async def test_tree_state_loaded_once_per_run_and_invalidated_on_compaction():
    """Step 3：三元组缓存——注入缓存后，多次 assemble 只全量加载一次树；
    压缩落盘（invalidate_tree_state）后必须重读。"""
    retriever = _FakeRetriever()
    session_store = _FakeSessionStore()
    coordinator = AssembleCoordinator(
        memory_retriever=retriever,
        state_store=_FakeStateStore(),
        memory_formatter=_FakeFormatter(),  # type: ignore[arg-type]
        task_profile_inference=TaskProfileInference(),
        session_store=session_store,  # type: ignore[arg-type]
    )
    coordinator.set_ports(_FakePorts())  # type: ignore[arg-type]
    cache = MemoryCoreCache(label="test")
    coordinator.set_memory_core_cache(cache)

    with patch(
        "prismclaw.engine.compression.assemble_coordinator.build_context_graph",
        return_value=None,
    ):
        await coordinator.assemble(session_id="s", messages=[], prompt="p")
        await coordinator.assemble(session_id="s", messages=[], prompt="p")
    assert session_store.load_calls == 1  # 第二次命中三元组缓存，不重载树

    cache.invalidate_tree_state("s")
    with patch(
        "prismclaw.engine.compression.assemble_coordinator.build_context_graph",
        return_value=None,
    ):
        await coordinator.assemble(session_id="s", messages=[], prompt="p")
    assert session_store.load_calls == 2  # 失效后重读