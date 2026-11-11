"""域模块发现、加载、编排。

通过 importlib.metadata.entry_points(group="prismclaw.domains") 发现域包，
按配置 domains.enabled 加载，校验版本和依赖后按序注入启动流程。
"""
from __future__ import annotations

import asyncio
import logging
from importlib.metadata import entry_points, version as pkg_version
from typing import Any

from prismclaw.schema.services.domain import (
    DomainBundle,
    DomainInfrastructureContext,
    DomainLifecycleContext,
)

logger = logging.getLogger(__name__)


class DomainRegistry:
    """发现、加载、编排 DomainBundle。

    对标 Pi Agent 的 PackageManager + Harness 的 Cordis Plugin Loader。
    """

    def __init__(self) -> None:
        self._bundles: dict[str, DomainBundle] = {}
        self._errors: dict[str, str] = {}

    def discover(self) -> list[DomainBundle]:
        """扫描 entry_points(group="prismclaw.domains")"""
        try:
            eps = entry_points(group="prismclaw.domains")
        except Exception:
            logger.debug("No prismclaw.domains entry_points found")
            return []

        bundles: list[DomainBundle] = []
        for ep in eps:
            try:
                factory = ep.load()
                bundle = factory() if callable(factory) else factory
                if not isinstance(bundle, DomainBundle):
                    logger.warning(
                        "Domain entry point '%s' does not implement DomainBundle", ep.name
                    )
                    continue
                bundles.append(bundle)
            except Exception as e:
                logger.error("Failed to load domain '%s': %s", ep.name, e)
                self._errors[ep.name] = str(e)
        return bundles

    def load_enabled(
        self,
        enabled: list[str],
        *,
        load_all_when_empty: bool = True,
    ) -> list[DomainBundle]:
        """按配置加载指定域，校验版本 + 依赖。

        返回按依赖拓扑排序后的域列表。

        ``load_all_when_empty`` 区分「enabled 为空」的两种语义：
        - True（默认，未编译/旧行为）：空列表 = 未指定，加载所有已安装域。
        - False（编译期已裁剪）：空列表 = 明确启用 0 个域，加载为空。
        调用方在成功 import 到 ``_capability_filters_generated.py``（即已运行
        过编译脚本）时传 False——此时 ``ENABLED_DOMAINS=[]`` 是权威的「0 个域」，
        而非「未指定」。
        """
        discovered = {b.name: b for b in self.discover()}

        # enabled 为空且未声明裁剪意图 → 加载所有已安装的域（旧行为，安装即启用）
        if not enabled and load_all_when_empty:
            enabled = list(discovered.keys())

        core_version = self._get_core_version()
        result: list[DomainBundle] = []

        for name in enabled:
            if name not in discovered:
                logger.error("Domain '%s' not found in installed packages", name)
                self._errors[name] = "not found"
                continue

            bundle = discovered[name]

            # 版本校验
            if bundle.required_core_version:
                try:
                    from packaging.specifiers import SpecifierSet
                    spec = SpecifierSet(bundle.required_core_version)
                    if core_version not in spec:
                        msg = f"requires core {bundle.required_core_version}, got {core_version}"
                        logger.error("Domain '%s' %s", name, msg)
                        self._errors[name] = msg
                        continue
                except Exception as e:
                    logger.warning("Domain '%s' version check failed: %s", name, e)

            # 依赖校验
            missing_deps = [d for d in bundle.depends_on if d not in enabled]
            if missing_deps:
                msg = f"depends on {missing_deps} which is not enabled"
                logger.error("Domain '%s' %s", name, msg)
                self._errors[name] = msg
                continue

            result.append(bundle)
            self._bundles[name] = bundle

        return result

    async def apply_infrastructure(
        self,
        bundles: list[DomainBundle],
        ctx: DomainInfrastructureContext,
    ) -> None:
        """基础设施阶段：注册持久化存储"""
        for b in bundles:
            try:
                result = b.on_build_infrastructure(ctx)
                if asyncio.iscoroutine(result):
                    await result
                logger.info("Domain '%s' infrastructure registered", b.name)
            except Exception as e:
                logger.exception("Domain '%s' on_build_infrastructure failed", b.name)
                self._errors[b.name] = str(e)

    def apply_runtime(
        self,
        bundles: list[DomainBundle],
        ctx_factory: Any,  # Callable[[str], DomainRuntimeContext]
    ) -> None:
        """运行时阶段：注册工具、钩子、治理规则（事务性）"""
        for b in bundles:
            ctx = ctx_factory(b.name)
            try:
                b.on_build_runtime(ctx)
                ctx.commit()
                logger.info("Domain '%s' runtime registered", b.name)
            except Exception as e:
                logger.exception("Domain '%s' on_build_runtime failed", b.name)
                self._errors[b.name] = str(e)

    def apply_lifecycle(
        self,
        bundles: list[DomainBundle],
        ctx: DomainLifecycleContext,
    ) -> None:
        """生命周期阶段：注册路由、启动后台任务、自动接长任务 emitter。

        路由挂载由调用方（_lifespan）统一执行，此处仅调用 bundle 的
        on_build_lifecycle 收集路由声明。额外：若 bundle 声明了
        task_store（带 StatusEmitterMixin 的长任务 store），且 web_deps
        可用 → 自动完成 emitter 接线（域 bundle 无需手写接线代码）。
        """
        for b in bundles:
            try:
                self._auto_wire_task_emitter(b, ctx)
                b.on_build_lifecycle(ctx)
                logger.info("Domain '%s' lifecycle registered", b.name)
            except Exception as e:
                logger.exception("Domain '%s' on_build_lifecycle failed", b.name)
                self._errors[b.name] = str(e)

    def _auto_wire_task_emitter(
        self,
        bundle: DomainBundle,
        ctx: DomainLifecycleContext,
    ) -> None:
        """域长任务 emitter 自动接线。

        前提：bundle.task_store 非空（带 StatusEmitterMixin） +
        web_deps.domain_task_emitter 可用。接线动作：调
        store.set_status_listener(adapter(bundle.name, emitter))。
        域命名空间参数化：kind = bundle.name（legal / medical / 自建 agent）。
        """
        store = getattr(bundle, "task_store", None)
        if store is None:
            return
        web_deps = getattr(getattr(ctx.app, "state", None), "web_deps", None)
        emitter = getattr(web_deps, "domain_task_emitter", None)
        if emitter is None:
            logger.info(
                "Domain '%s' 有长任务 store，但 web_deps 未启用 → 跳过 emitter 接线",
                bundle.name,
            )
            return
        try:
            from prismclaw.schema.services.domain_task_events import make_status_listener
            store.set_status_listener(make_status_listener(emitter, kind=bundle.name))
            logger.info("Domain '%s' 长任务 emitter 已自动接线 (kind=%s)", bundle.name, bundle.name)
        except Exception as e:
            logger.exception("Domain '%s' emitter 自动接线失败: %s", bundle.name, e)

    def _get_core_version(self) -> str:
        """获取 prismclaw 核心版本"""
        try:
            return pkg_version("prismclaw")
        except Exception:
            return "0.0.0"

    @property
    def errors(self) -> dict[str, str]:
        """返回域加载错误（可用于 GET /api/domains/status）"""
        return dict(self._errors)

    @property
    def loaded_bundles(self) -> list[DomainBundle]:
        """返回已成功加载的域列表"""
        return list(self._bundles.values())
