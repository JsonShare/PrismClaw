"""Turn 层记忆组合缓存（compression 层自有）。

记忆组合（检索 / 证据选择 / task_profile 推断 / 记忆预算）的输入不随
逐轮 assistant/tool 消息变化，可在一次 Run 内缓存。缓存为本地数据
结构，不含任何 execution 层依赖（dependency direction: compression →
foundation/schema only）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from prismclaw.engine.foundation.content_key import content_key
from prismclaw.engine.foundation.metrics import emit

__all__ = ["MemoryCore", "MemoryCoreCache", "memory_core_cache_key"]


@dataclass(slots=True)
class MemoryCore:
    """assemble 中与逐轮消息无关的中间产物集合。"""

    retrieval_query: str | None
    anchor_sources: list[str]
    evidence_strategy: Any
    context_graph: Any
    raw_memory_blocks: list[Any]
    task_profile: Any
    memory_plan: Any
    memory_budget_audit: Any
    computed_at: float = field(default_factory=time.monotonic)


def memory_core_cache_key(
    *,
    session_id: str,
    task_id: str | None,
    prompt: str | None,
    request_mode: str | None,
    history_artifact_id: str | None,
) -> str:
    """记忆组合缓存键：只含 Run 级稳定输入。

    - 逐轮消息增量不参与：session_state 滚动投影是 iter 层渲染
      （每轮进 LLM 上下文），不进入检索/profile 输入（Step 2 归位）。
    - token_budget 不参与：记忆预算分配按首次计算冻结（Run 级定额），
      history_budget 逐轮波动不触发重算。
    - history_artifact_id 变化（压缩重启）是唯一失效事件。
    """
    return content_key(
        session_id=session_id,
        payload={
            "task_id": task_id,
            "prompt": prompt,
            "request_mode": request_mode,
            "history_artifact_id": history_artifact_id,
        },
    )


class MemoryCoreCache:
    """Run 生命周期内的记忆组合缓存。

    只存最近计算结果（单 Run 内任务固定时仅压缩/状态写入会换键，
    LRU 无必要）；超过上界淘汰最旧条目，防异常路径膨胀。

    另承载 Run 级树状态三元组（persisted_artifacts / history_artifact /
    persisted_memory_state）缓存：三者仅在压缩落盘时变化，由压缩重启
    路径显式失效（Step 3），避免 assemble 每迭代全量重载会话树。
    """

    _MAX_ENTRIES = 8

    def __init__(self, *, label: str = "run") -> None:
        self._label = label
        self._store: dict[str, MemoryCore] = {}
        self._hits = 0
        self._misses = 0
        self._tree_state: dict[str, tuple[Any, Any, Any]] = {}

    def get(self, key: str) -> MemoryCore | None:
        core = self._store.get(key)
        if core is None:
            self._misses += 1
            emit(
                "projection_memory_core_total",
                1.0,
                kind="counter",
                labels={"outcome": "miss", "label": self._label},
            )
            return None
        self._hits += 1
        emit(
            "projection_memory_core_total",
            1.0,
            kind="counter",
            labels={"outcome": "hit", "label": self._label},
        )
        return core

    def put(self, key: str, core: MemoryCore) -> None:
        self._store[key] = core
        if len(self._store) > self._MAX_ENTRIES:
            oldest = min(self._store, key=lambda k: self._store[k].computed_at)
            del self._store[oldest]

    # ── Run 级树状态三元组缓存（Step 3）──

    def get_tree_state(self, session_id: str) -> tuple | None:
        """返回 (persisted_artifacts, history_artifact, persisted_memory_state)
        的 Run 级缓存；未缓存时 None。"""
        return self._tree_state.get(session_id)

    def put_tree_state(self, session_id: str, state: tuple) -> None:
        self._tree_state[session_id] = state

    def invalidate_tree_state(self, session_id: str | None = None) -> None:
        """压缩落盘后的显式失效（P2 单一真相源）；None 清全部（Run 收尾）。"""
        if session_id is None:
            self._tree_state.clear()
        else:
            self._tree_state.pop(session_id, None)

    @property
    def stats(self) -> tuple[int, int]:
        return self._hits, self._misses