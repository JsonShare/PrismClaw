from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from prismclaw.engine.compression.memory_retriever import MemoryRetrievalSettings
    from prismclaw.schema.content.context import TaskProfile


class DynamicContextRouter:
    """
    Dynamic Context Router: Intent-driven Quota Allocation.
    配额只由意图维度（prefers_tooling / requires_grounding）与配置偏置决定；
    轮次维度已删除——「首轮/续轮」不是意图，收窄改由 requires_grounding 表达。
    """

    @classmethod
    def allocate_quotas(
        cls,
        task_profile: TaskProfile,
        settings: MemoryRetrievalSettings,
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
        else:
            # DD-9：不需要取证的任务不翻阅归档。收窄发生在检索阶段，
            # 装配后不再补刀（DD-10）——否则归档超量既不被剔除也不降级，
            # 只剩条数限制失效。
            archive_max = min(archive_max, 1 if settings.archive_max_results > 0 else 0)

        return l2_limit, l3_limit, archive_max
