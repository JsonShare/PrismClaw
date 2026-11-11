"""Run 生命周期投影协调器（Phase 1）。

持有 turn 层记忆组合缓存，并在装配适配器上注入。注入是幂等的；
装配协调器未提供注入接口时 attach 为空操作（缓存不生效，装配
行为退化为无缓存路径）。
"""
from __future__ import annotations

from typing import Any

from prismclaw.engine.compression.memory_assembly_cache import MemoryCoreCache


class RunProjector:
    """一次 run 的投影协调器。

    职责边界刻意收窄：只负责缓存持有与注入，不参与请求打包（打包
    仍在既有 pipeline 中）。后续 Phase 的 RunPlan / 端口化在此包上
    扩展，而非回填到主循环。
    """

    def __init__(self, *, label: str = "run") -> None:
        self._cache = MemoryCoreCache(label=label)

    def attach(self, assembler: Any) -> None:
        """把记忆组合缓存注入装配协调器（幂等）。"""
        setter = getattr(assembler, "set_memory_core_cache", None)
        if setter is None:
            return
        setter(self._cache)

    def invalidate(self, session_id: str | None = None) -> None:
        """压缩落盘后的显式失效（Step 3）：树状态三元组与换键重算。

        由 prepare 端口的压缩重启分支调用——压缩产物已落树，下一轮
        assemble 必须重读树（新 CompactionEntry 进检索锚）。
        """
        self._cache.invalidate_tree_state(session_id)

    @property
    def cache_stats(self) -> tuple[int, int]:
        return self._cache.stats