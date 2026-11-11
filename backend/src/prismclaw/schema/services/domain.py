"""业务域模块接入平台的统一协议。

DomainBundle 是业务域（医疗/法律/Office）接入 PrismClaw 的标准协议。
对标 Pi Agent 的 PiManifest + Harness 的 Cordis Plugin —
一个域模块通过实现此协议，向平台注册工具、钩子、治理规则、持久化存储和路由。

依赖方向：engine/assembly → schema（Protocol 定义在 schema，实现在 engine/assembly）
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@dataclass
class MenuEntry:
    """工作台内嵌菜单项描述。

    当 DomainManifest.menus 长度 <= 1 时，工作台不显示左侧菜单，
    直接全屏渲染功能页面，此时 MenuEntry 仅用于声明默认 Tab key。
    """
    key: str
    label: str
    icon: str = ""
    parent: str = ""


@dataclass
class PageEntry:
    """前端页面组件描述"""
    key: str
    path: str
    js_asset: str = ""   # 前端组件 bundle URL
    css_asset: str = ""   # 前端样式 URL


@dataclass
class DomainManifest:
    """域模块前端描述 — GET /api/domains/manifests 返回此结构

    两种渲染模式（自动判定，无需显式声明）：
    - 工作台模式：menus >= 2 项 → 侧边栏一级入口 → 左侧内嵌二级菜单 + 右侧功能页
    - 独立功能模式：menus 为空或仅 1 项 → 侧边栏一级入口 → 全屏渲染功能页面，不显示左侧菜单

    path 始终指向工作台/功能页面路由，如 "/medical" 或 "/legal"。
    """
    key: str
    label: str
    icon: str = ""
    path: str = ""           # 工作台/功能页面路由
    menus: list[MenuEntry] = field(default_factory=list)
    pages: list[PageEntry] = field(default_factory=list)


class DomainInfrastructureContext:
    """基础设施注册上下文 — 传递给 DomainBundle.on_build_infrastructure()。

    提供平台基础设施引用，域模块用它创建自己的存储。
    """

    def __init__(
        self,
        db_engine: Any = None,
        redis_client: Any = None,
        session_factory: Any = None,
        settings: Any = None,
        app: Any = None,
    ) -> None:
        self._db_engine = db_engine
        self._redis_client = redis_client
        self._session_factory = session_factory
        self._settings = settings
        self._app = app
        self._domain_stores: dict[str, Any] = {}

    @property
    def db_engine(self) -> Any:
        return self._db_engine

    @property
    def redis_client(self) -> Any:
        return self._redis_client

    @property
    def session_factory(self) -> Any:
        return self._session_factory

    @property
    def settings(self) -> Any:
        return self._settings

    @property
    def app(self) -> Any:
        return self._app

    @property
    def domain_stores(self) -> dict[str, Any]:
        return self._domain_stores

    def register_store(self, name: str, store: Any) -> None:
        """注册域级持久化存储"""
        self._domain_stores[name] = store


class DomainRuntimeContext:
    """运行时注册上下文 — 传递给 DomainBundle.on_build_runtime()。

    两阶段注册：register_*() 暂存，commit() 一次性提交。
    提交失败自动回滚。
    """

    def __init__(
        self,
        domain_name: str,
        tool_registry: Any = None,
        hook_dispatcher: Any = None,
        governance_reducers: list[Any] | None = None,
        skill_provider: Any = None,
        *,
        tool_wrapper: Any = None,
        hook_wrapper: Any = None,
        domain_stores: dict[str, Any] | None = None,
        capability_filter: dict[str, dict[str, bool]] | None = None,
    ) -> None:
        self._domain_name = domain_name
        self._tool_registry = tool_registry
        self._hook_dispatcher = hook_dispatcher
        self._governance_reducers = governance_reducers if governance_reducers is not None else []
        self._skill_provider = skill_provider
        # 故障隔离包装器（由 engine 层注入，避免 schema → engine 反向依赖）
        self._tool_wrapper = tool_wrapper
        self._hook_wrapper = hook_wrapper
        self._domain_stores = domain_stores or {}
        # 能力开关：{"tools": {name: bool}, "skills": {name: bool}}
        # 未列出 = 默认启用（opt-out）；由 domains.yaml 编译产物注入
        self._capability_filter = capability_filter
        # 暂存
        self._pending_tools: list[Any] = []
        self._pending_hooks: list[tuple[Any, Any]] = []
        self._pending_governance: list[Any] = []
        self._pending_skills: list[Any] = []
        self._pending_registry_cleanups: list[Callable[[], None]] = []
        self._committed = False

    @property
    def domain_name(self) -> str:
        return self._domain_name

    @property
    def domain_stores(self) -> dict[str, Any]:
        """基础设施阶段注册的域存储映射，供运行时工具引用。"""
        return self._domain_stores

    def register_tool(self, tool: Any) -> None:
        """暂存工具注册（commit 时生效）。

        若 capability_filter 标记该工具为 disabled，跳过注册（不入 pending）。
        未在 filter 中列出的工具默认启用。
        """
        if self._capability_filter:
            tools_filter = self._capability_filter.get("tools", {})
            if not tools_filter.get(tool.name, True):
                logger.info("工具 %s 被开关关闭，跳过注册", tool.name)
                return
        self._pending_tools.append(tool)

    def register_hook(self, event: Any, handler: Any) -> None:
        """暂存钩子注册（commit 时生效）"""
        self._pending_hooks.append((event, handler))

    def register_governance(self, reducer: Any) -> None:
        """暂存治理规则注册（commit 时生效）"""
        self._pending_governance.append(reducer)

    def register_skill(self, skill_manifest: Any) -> None:
        """暂存域技能注册（commit 时生效）。

        域通过此方法将 SKILL.md 解析后的 SkillManifest 注入平台技能目录。
        commit 时写入 _skill_provider 的动态技能表，下次 list_skills 即可见。

        若 capability_filter 标记该技能为 disabled，跳过注册。
        未在 filter 中列出的技能默认启用。
        """
        if self._capability_filter:
            skills_filter = self._capability_filter.get("skills", {})
            if not skills_filter.get(skill_manifest.name, True):
                logger.info("技能 %s 被开关关闭，跳过注册", skill_manifest.name)
                return
        self._pending_skills.append(skill_manifest)

    def defer_registry_cleanup(self, cleanup_fn: Callable[[], None]) -> None:
        """延迟注册全局注册表的清理回调——commit 失败回滚时逆序执行。

        用于绕过 ctx 两阶段提交、直接调用全局 register_*() 的域扩展点
        （证据推断器、免责声明、敏感扩展、PHI 模式等）。
        域模块在调用全局 register_*() 后立即调用此方法，
        传入对应的 unregister_*() lambda，确保回滚时全局状态一致。
        """
        self._pending_registry_cleanups.append(cleanup_fn)

    def commit(self) -> None:
        """一次性提交所有注册，任一失败自动回滚。

        工具和钩子会被 DomainIsolatedTool/DomainIsolatedHook 包裹，
        确保域模块的异常不传播到核心执行流程。
        """
        if self._committed:
            return
        committed_tools: list[str] = []
        committed_hooks: list[tuple[Any, Any]] = []
        committed_governance_count = 0
        committed_skills: list[str] = []
        try:
            # 注册 tools（可选包裹故障隔离层）
            for tool in self._pending_tools:
                wrapped = self._tool_wrapper(tool, self._domain_name) if self._tool_wrapper else tool
                if self._tool_registry is not None:
                    self._tool_registry.register(wrapped)
                committed_tools.append(wrapped.name)
            # 注册 hooks（可选包裹故障隔离层）
            for event, handler in self._pending_hooks:
                wrapped = self._hook_wrapper(handler, self._domain_name) if self._hook_wrapper else handler
                if self._hook_dispatcher is not None:
                    self._hook_dispatcher.register(event, wrapped)
                committed_hooks.append((event, wrapped))
            # 追加 governance reducers
            self._governance_reducers.extend(self._pending_governance)
            committed_governance_count = len(self._pending_governance)
            # 注册域技能（注入 _skill_provider 的动态技能表）
            for skill_manifest in self._pending_skills:
                if self._skill_provider is not None:
                    self._skill_provider._dynamic_state.dynamic_skills[skill_manifest.name] = skill_manifest
                committed_skills.append(skill_manifest.name)
            self._committed = True
        except Exception:
            self._rollback(committed_tools, committed_hooks, committed_governance_count, committed_skills)
            raise

    def _rollback(
        self,
        tools: list[str],
        hooks: list[tuple[Any, Any]],
        governance_count: int,
        skills: list[str] | None = None,
    ) -> None:
        """回滚已注册内容"""
        for name in tools:
            if self._tool_registry is not None:
                try:
                    self._tool_registry.unregister(name)
                except Exception:
                    pass
        for event, handler in hooks:
            if self._hook_dispatcher is not None:
                try:
                    self._hook_dispatcher.unregister(event, handler)
                except Exception:
                    pass
        # 回滚 governance
        for _ in range(governance_count):
            if self._governance_reducers:
                self._governance_reducers.pop()
        # 回滚域技能
        for name in skills or []:
            if self._skill_provider is not None:
                try:
                    self._skill_provider._dynamic_state.dynamic_skills.pop(name, None)
                except Exception:
                    pass
        # 回滚全局注册表清理（逆序）
        for cleanup_fn in reversed(self._pending_registry_cleanups):
            try:
                cleanup_fn()
            except Exception:
                pass
        self._pending_registry_cleanups.clear()


class DomainLifecycleContext:
    """生命周期注册上下文 — 传递给 DomainBundle.on_build_lifecycle()。"""

    def __init__(
        self,
        app: Any = None,
        settings: Any = None,
        task_manager: Any = None,
    ) -> None:
        self._app = app
        self._settings = settings
        self._task_manager = task_manager
        self._domain_routers: list[tuple[Any, str]] = []

    @property
    def app(self) -> Any:
        return self._app

    @property
    def settings(self) -> Any:
        return self._settings

    def register_route(self, router: Any, prefix: str = "") -> None:
        """注册域级 API 路由"""
        self._domain_routers.append((router, prefix))

    def spawn_background_task(self, name: str, coro: Any) -> str | None:
        """启动后台任务（需要 task_manager）"""
        if self._task_manager is not None:
            return self._task_manager.spawn(name, coro)
        return None

    @property
    def domain_routers(self) -> list[tuple[Any, str]]:
        return self._domain_routers


@runtime_checkable
class DomainBundle(Protocol):
    """业务域模块接入平台的统一协议。

    一个业务域（如 medical / legal / office / 自建 agent）通过实现此 Protocol，
    向 PrismClaw 注册：
    - 持久化存储（on_build_infrastructure）
    - 工具、钩子、治理规则（on_build_runtime）
    - API 路由、后台任务（on_build_lifecycle）
    - 前端菜单/页面描述（get_manifest）
    - 长任务 store（task_store 可选属性 → Registry 自动接 emitter）

    通过 entry_points(group="prismclaw.domains") 发现，
    配置 domains.enabled 启用。
    """

    name: str
    """域名标识：medical / legal / office"""

    required_core_version: str
    """核心版本约束，如 ">=0.1.0,<2.0.0" """

    depends_on: list[str]
    """依赖的其他域，如 ["legal"] """

    task_store: Any | None = None
    """可选：域长任务 store 实例（需继承 StatusEmitterMixin）。

    声明后 DomainRegistry.apply_lifecycle 会自动完成 emitter 接线
    （web_deps 可用时），域 bundle 无需手写接线代码。不声明则不接。
    """

    def on_build_infrastructure(self, ctx: DomainInfrastructureContext) -> None:
        """基础设施构建时调用 — 注册持久化存储"""
        ...

    def on_build_runtime(self, ctx: DomainRuntimeContext) -> None:
        """运行时构建时调用 — 注册工具、钩子、治理规则"""
        ...

    def on_build_lifecycle(self, ctx: DomainLifecycleContext) -> None:
        """生命周期构建时调用 — 注册路由、启动后台任务"""
        ...

    def get_manifest(self) -> DomainManifest:
        """返回前端菜单/页面描述"""
        ...


@runtime_checkable
class DomainTaskEmitter(Protocol):
    """域任务状态事件广播端口。

    域层把任务状态变更转成事件后，经此端口推送给在线用户的 WS 连接。
    实现在 web 层（WsDomainTaskEmitter）；域模块不得导入 web 层。
    """

    async def broadcast(self, user_id: str, event_type: str, data: dict[str, Any]) -> None:
        """向指定用户的全部 WS 连接广播一帧域任务事件。"""
        ...
