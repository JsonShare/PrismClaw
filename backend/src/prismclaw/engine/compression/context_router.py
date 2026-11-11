from __future__ import annotations

from typing import TYPE_CHECKING

from prismclaw.schema.llm.request_mode import RequestMode

if TYPE_CHECKING:
    from prismclaw.engine.compression.memory_retriever import MemoryRetrievalSettings
    from prismclaw.schema.content.context import TaskProfile


class DynamicContextRouter:
    """
    Dynamic Context Router: Intent-driven Quota Allocation.
    Replaces static configuration limits with dynamic token and retrieval quotas
    based on the current execution intent and request mode.
    """

    @classmethod
    def allocate_quotas(
        cls,
        task_profile: TaskProfile,
        settings: MemoryRetrievalSettings,
        request_mode: RequestMode | None = None,
    ) -> tuple[int, int, int]:
        """
        Returns (l2_quota, l3_quota, archive_limit).
        L2: Fact memory (declarative)
        L3: Procedure memory (SOPs, workflows)
        """
        l2_limit = max(0, settings.search_l2_quota)
        l3_limit = max(0, settings.search_l3_quota)
        archive_max = max(0, settings.archive_max_results)

        bias = getattr(settings, "intent_bias", None) or {}

        def _bias(key: str) -> int:
            # 纯配置读取：缺失键取 0 增量（不再回退旧版硬编码配额表）。
            try:
                return int(bias[key])
            except (KeyError, TypeError, ValueError):
                return 0

        # 意图×目标维度的加项全部来自 memory.intent_bias：
        #   prefers_tooling  → L3 偏置（流程/工具用法）
        #   requires_grounding → L2 + archive 偏置（事实/归档/证据）
        if task_profile.prefers_tooling:
            l3_limit = max(0, l3_limit + _bias("prefers_tooling_l3"))
            l2_limit = max(0, l2_limit + _bias("prefers_tooling_l2"))
            archive_max = max(0, archive_max + _bias("prefers_tooling_archive"))
        if task_profile.requires_grounding:
            l2_limit = max(0, l2_limit + _bias("requires_grounding_l2"))
            archive_max = max(0, archive_max + _bias("requires_grounding_archive"))
            l3_limit = max(0, l3_limit + _bias("requires_grounding_l3"))

        if request_mode == "cold_start":
            archive_max = min(archive_max, 1 if settings.archive_max_results > 0 else 0)
            l3_limit = max(0, l3_limit + _bias("cold_start_l3"))
        elif request_mode == "follow_up":
            l2_limit = max(0, l2_limit + _bias("follow_up_l2"))
            l3_limit = max(0, l3_limit + _bias("follow_up_l3"))
            archive_max = 0

        return l2_limit, l3_limit, archive_max
