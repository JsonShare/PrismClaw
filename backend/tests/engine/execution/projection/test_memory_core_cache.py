"""三周期投影（Phase 1）单元测试：内容寻址键 + 记忆组合缓存语义。"""
from __future__ import annotations

from prismclaw.engine.compression.memory_assembly_cache import (
    MemoryCoreCache,
    memory_core_cache_key,
)
from prismclaw.engine.foundation.content_key import content_key


class TestContentKey:
    def test_stable_across_dict_order(self):
        a = content_key(session_id="s", payload={"x": 1, "y": [1, 2, 3]})
        b = content_key(session_id="s", payload={"y": [1, 2, 3], "x": 1})
        assert a == b

    def test_sensitive_to_any_input(self):
        base = content_key(session_id="s", payload={"prompt": "a", "n": 1})
        assert base != content_key(session_id="s", payload={"prompt": "a", "n": 2})
        assert base != content_key(session_id="s", payload={"prompt": "b", "n": 1})
        assert base != content_key(session_id="s2", payload={"prompt": "a", "n": 1})

    def test_tolerates_non_json_objects(self):
        # default=str 兜底：不可序列化对象被稳定字符串化
        key = content_key(session_id="s", payload={"obj": object()})
        assert isinstance(key, str) and key.startswith("s:")


class TestMemoryCoreCacheKey:
    def test_key_is_run_scoped_stable_inputs_only(self):
        # Step 2 归位：键只含 Run 级稳定输入（session/task/prompt/mode/压缩产物），
        # 逐轮消息增量与逐轮 history_budget 均不参与。
        base = memory_core_cache_key(
            session_id="s",
            task_id="t",
            prompt="p",
            request_mode="work",
            history_artifact_id=None,
        )
        same = memory_core_cache_key(
            session_id="s",
            task_id="t",
            prompt="p",
            request_mode="work",
            history_artifact_id=None,
        )
        assert base == same

    def test_sensitive_to_run_level_inputs_and_artifact(self):
        def key(*, prompt="p", artifact=None, task_id="t", mode="work"):
            return memory_core_cache_key(
                session_id="s",
                task_id=task_id,
                prompt=prompt,
                request_mode=mode,
                history_artifact_id=artifact,
            )

        base = key()
        assert base != key(prompt="p2")  # 任务变化 → 重算
        assert base != key(task_id="t2")  # 任务标识变化 → 重算
        assert base != key(mode="chat")  # 模式变化 → 重算
        assert base != key(artifact="art-1")  # 历史压缩产物变化（唯一失效事件）→ 重算


class TestMemoryCoreCache:
    def test_hit_and_miss_counters(self):
        cache = MemoryCoreCache(label="test")
        assert cache.get("k") is None
        cache.put("k", _core(1.0))
        assert cache.get("k") is not None
        hits, misses = cache.stats
        assert hits == 1 and misses == 1

    def test_evicts_oldest_beyond_bound(self):
        cache = MemoryCoreCache(label="test")
        for i in range(10):
            cache.put(f"k{i}", _core(float(i)))
        assert cache.get("k0") is None  # 最旧的被淘汰
        assert cache.get("k9") is not None
        assert cache.get("k3") is not None


def _core(stamp: float):
    from prismclaw.engine.compression.memory_assembly_cache import MemoryCore

    return MemoryCore(
        retrieval_query=None,
        anchor_sources=[],
        evidence_strategy=None,
        context_graph=None,
        raw_memory_blocks=[],
        task_profile=None,
        memory_plan=None,
        memory_budget_audit=None,
        computed_at=stamp,
    )