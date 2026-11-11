"""投影层：Run/Turn/Iteration 三周期装配的循环侧协调器。

本包只承载循环侧职责（RunProjector 持有缓存并注入装配适配器）；
记忆组合缓存本体与内容键位于 compression / foundation 层
（依赖方向：loop → compression → foundation/schema）。
"""
from __future__ import annotations

from prismclaw.engine.compression.memory_assembly_cache import (
    MemoryCore,
    MemoryCoreCache,
)
from prismclaw.engine.execution.projection.project import RunProjector

__all__ = [
    "MemoryCore",
    "MemoryCoreCache",
    "RunProjector",
]