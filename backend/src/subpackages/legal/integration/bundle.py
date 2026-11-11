"""法律域 Bundle —— 实现 DomainBundle Protocol，注册法律域能力。

当前为最小骨架（仅让侧边栏菜单可见）：
- on_build_infrastructure / on_build_runtime / on_build_lifecycle 均为空实现
- get_manifest 从编译产物读取前端菜单/页面描述（与 science 域一致，
  唯一真实来源是 scripts/compile/domains.yaml）

后续接入域能力时，在对应阶段注册持久化存储 / 工具 / 路由即可。
"""
from __future__ import annotations

import logging
from typing import Any

from prismclaw.schema.services.domain import (
    DomainInfrastructureContext,
    DomainLifecycleContext,
    DomainManifest,
    DomainRuntimeContext,
)

logger = logging.getLogger(__name__)


class LegalBundle:
    """法律域 Bundle —— 当前仅提供页面入口与菜单描述。"""

    name = "legal"
    required_core_version = ">=0.1.0,<2.0.0"
    depends_on: list[str] = []
    task_store: Any | None = None  # Protocol 数据成员；本域无长任务 store

    # ── 基础设施：暂空（骨架阶段无持久化存储） ─────────────────────

    def on_build_infrastructure(self, ctx: DomainInfrastructureContext) -> None:
        """骨架阶段无域级存储。"""

    # ── 运行时：暂空（骨架阶段无工具/钩子/治理规则） ──────────────

    def on_build_runtime(self, ctx: DomainRuntimeContext) -> None:
        """骨架阶段无工具/钩子/治理规则。"""
        ctx.commit()

    # ── 生命周期：暂空（骨架阶段无域路由） ───────────────────────

    def on_build_lifecycle(self, ctx: DomainLifecycleContext) -> None:
        """骨架阶段无域级 REST 路由。"""

    # ── 前端菜单/页面描述 ──────────────────────────────────────

    def get_manifest(self) -> DomainManifest:
        """返回法律域前端菜单与页面描述（由 domains.yaml 编译生成）。"""
        from .._manifest_generated import MANIFEST
        return MANIFEST
