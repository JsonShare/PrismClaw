"""外部环境的读侧快照：专家目录与能力策略的唯一读取入口。

写入/热更新仍由 CapabilityPolicyRegistry 与 ExpertLifecycleManager 承担；
本模块只负责把"当前有哪些专家、当前哪些能力被允许"组合成一份不可变快照，
供一次 run 的作用域构建使用，避免各消费点分别读取、各自缓存。
"""

from __future__ import annotations

from dataclasses import dataclass

from prismclaw.engine.experts.registry.expert_registry import ExpertRegistry
from prismclaw.infra.config.capability_policy import (
    CapabilityPolicyRegistry,
    CapabilityPolicySnapshot,
)


@dataclass(frozen=True, slots=True)
class EnvironmentCatalogSnapshot:
    """一次 run 可见的外部环境。"""

    expert_registry: ExpertRegistry
    capability_policy: CapabilityPolicySnapshot


class EnvironmentCatalog:
    """组合专家目录与能力策略，按需生成一致快照。"""

    def __init__(
        self,
        *,
        expert_registry: ExpertRegistry,
        capability_policy_registry: CapabilityPolicyRegistry,
    ) -> None:
        self._expert_registry = expert_registry
        self._capability_policy_registry = capability_policy_registry

    def snapshot(self) -> EnvironmentCatalogSnapshot:
        """返回当前快照；策略文件的 mtime 检查由 CapabilityPolicyRegistry 自行节流。"""
        return EnvironmentCatalogSnapshot(
            expert_registry=self._expert_registry,
            capability_policy=self._capability_policy_registry.snapshot(),
        )


__all__ = [
    "EnvironmentCatalog",
    "EnvironmentCatalogSnapshot",
]
