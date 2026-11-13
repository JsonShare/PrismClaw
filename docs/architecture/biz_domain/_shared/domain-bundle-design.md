# PrismClaw 业务域插件架构总纲

## 一、目标

安装一个 pip 包 + 配置启用，即可获得完整的后端能力和前端 UI。

```
pip install prismclaw-medical
domains: enabled: [medical]
→ 后端：工具/钩子/治理/路由/存储 全部就绪
→ 前端：侧边栏菜单/工作台页面/组件 全部渲染
```

---

## 二、核心原则

| 原则 | 说明 |
|------|------|
| **Agent 驱动** | 所有域功能的核心执行引擎是系统 Agent，域不自建 LLM 调用。域插件只做两件事：(1) 给 Agent 注入能力 (2) 提供页面入口 |
| **技能即规范** | 域通过 SKILL.md 向 Agent 注入领域知识和操作流程，Agent 依据技能规范执行 |
| **工具即能力** | 域注册的工具就是 Agent 可调用的能力，工具定义决定了 Agent 能做什么 |
| **双入口** | 同一个能力，页面 REST API 和聊天工具调用共享同一套工具和技能。页面操作 → REST API → Agent；聊天对话 → Agent 识别意图 → 调用工具 |
| **故障隔离** | 域工具/钩子异常不拖垮核心引擎，通过 `DomainIsolatedTool` / `DomainIsolatedHook` 自动包裹 |
| **事务性注册** | 所有注册采用两阶段：`register_*` 暂存，`commit` 一次性提交，失败自动回滚 |
| **版本校验** | 每个域声明 `required_core_version`，`DomainRegistry` 启动时校验兼容性 |

设计约束：

1. 不拆 prismclaw — 平台 SDK 整体迭代
2. 技能插件体系不动 — DomainBundle 是应用级插件，与技能插件互补
3. 改造最小化 — 补扩展点，不重写
4. 前后端一体 — 一个 pip 包同时包含后端能力和前端 UI

---

## 三、执行模型

所有域功能的核心执行引擎是系统 Agent，域不自建 LLM 调用。

```
页面操作 → REST API → create_domain_session() → Agent Loop → 返回结果
聊天对话 → Agent Loop 识别意图 → 调用域工具 → 返回结果
```

域插件只做两件事：
1. **给 Agent 注入能力** — 工具、技能、钩子、治理规则
2. **提供页面入口** — REST 路由 + 前端菜单/页面

### 双入口共享

同一个域能力，页面 REST API 和聊天工具调用共享：

```
                    ┌─────────────────────────────────┐
                    │         域工具 + 技能            │
                    │  (smart_medical_record, etc.)     │
                    └───────────┬─────────────────────┘
                                │
               ┌────────────────┼────────────────┐
               │                │                │
        页面 REST API     Agent Loop 直接调用    钩子/治理
        (前端触发)        (聊天触发)            (自动生效)
```

---

## 四、架构约束

### 依赖方向

```
schema  <──  engine  <──  assembly  <──  app
                <──  persistence
```

engine 不导入 wiring/app/persistence（0 处），persistence 不导入 engine（0 处）。DomainBundle 在 wiring 层注入。

### 启动时序约束

`_lifespan.py` 启动共 41 步。关键约束：

1. redis_client/db_engine 在 `build_app_infrastructure_bundle` 内部才创建 → 不可在此之前插入域逻辑
2. `create_agent_runner_deps` 是原子操作 → 不可在中间插入
3. trajectory/scheduler 的"先建后补"模式已有时序 bug → DomainBundle 应在正确层级一次性组装

### Engine 内部耦合

execution ↔ compression 互引 32 行，是唯一的双向紧耦合。**结论：prismclaw 不拆包。**

---

## 五、现有扩展点缺口

| 扩展点 | 现有能力 | 缺口 |
|--------|---------|------|
| ToolRegistry | register/unregister | _lifespan 不暴露给外部 |
| HookDispatcher | 16 种事件 | register_extension_hooks 仅初始化调用一次 |
| ToolGovernance | 7 个内置 Reducer，Protocol 可扩展 | 所有调用点 governance=None |
| Plugin 管线 | Discovery→Load→Activate | SDK @tool 断裂；不支持 routes/governance |
| SkillRuntime | 渐进式披露 | 无域级 skill 路径注入 |

### 治理链断链

从"域模块注册 Reducer"到"Reducer 生效"之间有 5 个断点：

| 断点 | 位置 | 现状 |
|------|------|------|
| A | `loop/deps.py` AgentRunnerDeps | 无 governance 字段 |
| B | `runner_assembly.py` assemble_runner_deps | 未传入 governance |
| C | `governance.py` ToolGovernance.__init__ | reducers or (默认7个) 是替换而非追加 |
| D | `pre_request_pipeline.py` 等 4 个调用点 | governance=None |
| E | `schema/services/domain.py` | 不存在 |

---

## 六、DomainBundle Protocol

```python
# prismclaw/schema/services/domain.py

from __future__ import annotations
from typing import Protocol, runtime_checkable, Any
from dataclasses import dataclass, field


@runtime_checkable
class DomainBundle(Protocol):
    """业务域接入平台的统一协议。

    一个业务域（如医疗、法律）通过实现此 Protocol，向 PrismClaw 注册：
    - 持久化存储（on_build_infrastructure）
    - 工具、钩子、治理规则（on_build_runtime）
    - API 路由、后台任务（on_build_lifecycle）
    - 前端菜单/页面描述（get_manifest）

    通过 entry_points(group="prismclaw.domains") 发现，
    配置 domains.enabled 启用。
    """

    name: str                                    # "medical"
    required_core_version: str                   # ">=0.1.0,<2.0.0"
    depends_on: list[str]                        # ["legal"]

    def get_manifest(self) -> DomainManifest: ...
    def on_build_infrastructure(self, ctx: DomainInfrastructureContext) -> None: ...
    def on_build_runtime(self, ctx: DomainRuntimeContext) -> None: ...
    def on_build_lifecycle(self, ctx: DomainLifecycleContext) -> None: ...


# ── 前端描述模型 ──

@dataclass
class MenuEntry:
    """工作台内嵌菜单项描述 — 不再需要 path，功能页面在工作台内 Tab 切换。

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
```

---

## 七、工作台/独立功能双模式

DomainManifest 根据菜单数量自动判定渲染模式，域开发者无需显式声明。

### 工作台模式（menus >= 2）

侧边栏一级入口 → 工作台页面，内嵌左侧二级菜单 + 右侧功能页，功能页通过 Tab 切换。

```
┌──────────┬─────────────────────────────────────────────┐
│          │  医疗工作台                                   │
│  侧边栏   │  ┌──────────┬──────────────────────────────┐ │
│          │  │ 二级菜单  │  功能页面                       │ │
│ 💬 对话   │  │          │                              │ │
│ 📋 任务   │  │ ✅ 智能病历│  病历整理界面...               │ │
│ 📁 文件   │  │ 🌐 医学翻译│                              │ │
│ ⚙️ 设置   │  │ 📖 搜指南  │                              │ │
│          │  │ 💊 搜药品  │                              │ │
│ 🏥 医疗   │  │ 🔍 搜文献  │                              │ │
│          │  │          │                              │ │
│          │  └──────────┴──────────────────────────────┘ │
└──────────┴─────────────────────────────────────────────┘
```

路由模式：`/medical?tab=record`

- `/medical` → 默认显示第一个菜单项
- `/medical?tab=translate` → 切换到"医学翻译"
- `/medical?tab=guideline` → 切换到"搜指南"

### 独立功能模式（menus <= 1）

侧边栏一级入口 → 全屏渲染功能页面，不显示左侧菜单。

```
┌──────────┬─────────────────────────────────────────────┐
│          │                                             │
│  侧边栏   │  功能页面（全屏渲染）                          │
│          │                                             │
│ 💬 对话   │  无左侧菜单，直接展示内容                     │
│ 📋 任务   │                                             │
│ 📁 文件   │                                             │
│ ⚙️ 设置   │                                             │
│          │                                             │
│ 📄 某功能  │                                             │
│          │                                             │
└──────────┴─────────────────────────────────────────────┘
```

路由模式：`/feature`（无 tab 参数）

### MenuEntry 设计

- **无 path 字段** — 功能页面在工作台内 Tab 切换，不需要独立路由
- `key` 字段用于 URL query 参数（如 `?tab=record`）
- 当 menus <= 1 时，MenuEntry 仅用于声明默认 key

### DomainManifest.path

- 始终指向工作台/功能页面路由
- 工作台模式：如 `"/medical"`、`"/legal"`
- 独立功能模式：如 `"/export"`

### 路由设计总表

| 模式 | menus 数量 | 路由 | Tab 切换 |
|------|-----------|------|---------|
| 工作台 | >= 2 | `/{domain}` | `/{domain}?tab={key}` |
| 独立功能 | <= 1 | `/{domain}` | 无 |

---

## 八、三个注册上下文

两阶段注册：`register_*` 暂存，`commit` 一次性提交。提交失败自动回滚。

```python
# prismclaw/schema/services/domain.py

class DomainInfrastructureContext:
    """基础设施注册上下文 — 注册持久化存储。

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
    def db_engine(self) -> Any: return self._db_engine
    @property
    def redis_client(self) -> Any: return self._redis_client
    @property
    def session_factory(self) -> Any: return self._session_factory
    @property
    def settings(self) -> Any: return self._settings
    @property
    def app(self) -> Any: return self._app
    @property
    def domain_stores(self) -> dict[str, Any]: return self._domain_stores

    def register_store(self, name: str, store: Any) -> None:
        """注册域级持久化存储"""
        self._domain_stores[name] = store


class DomainRuntimeContext:
    """运行时注册上下文 — 注册工具/钩子/治理规则。

    两阶段注册：register_*() 暂存，commit() 一次性提交。
    提交失败自动回滚。
    工具和钩子会被 DomainIsolatedTool/DomainIsolatedHook 包裹，
    确保域模块的异常不传播到核心执行流程。
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
    ) -> None:
        self._domain_name = domain_name
        self._tool_registry = tool_registry
        self._hook_dispatcher = hook_dispatcher
        self._governance_reducers = governance_reducers if governance_reducers is not None else []
        self._skill_provider = skill_provider
        # 故障隔离包装器（由 engine 层注入，避免 schema → engine 反向依赖）
        self._tool_wrapper = tool_wrapper
        self._hook_wrapper = hook_wrapper
        # 暂存
        self._pending_tools: list[Any] = []
        self._pending_hooks: list[tuple[Any, Any]] = []
        self._pending_governance: list[Any] = []
        self._committed = False

    @property
    def domain_name(self) -> str: return self._domain_name

    def register_tool(self, tool: Any) -> None:
        """暂存工具注册（commit 时生效）"""
        self._pending_tools.append(tool)

    def register_hook(self, event: Any, handler: Any) -> None:
        """暂存钩子注册（commit 时生效）"""
        self._pending_hooks.append((event, handler))

    def register_governance(self, reducer: Any) -> None:
        """暂存治理规则注册（commit 时生效）"""
        self._pending_governance.append(reducer)

    def commit(self) -> None:
        """一次性提交所有注册，任一失败自动回滚。"""
        if self._committed:
            return
        committed_tools: list[str] = []
        committed_hooks: list[tuple[Any, Any]] = []
        committed_governance_count = 0
        try:
            for tool in self._pending_tools:
                wrapped = self._tool_wrapper(tool, self._domain_name) if self._tool_wrapper else tool
                if self._tool_registry is not None:
                    self._tool_registry.register(wrapped)
                committed_tools.append(wrapped.name)
            for event, handler in self._pending_hooks:
                wrapped = self._hook_wrapper(handler, self._domain_name) if self._hook_wrapper else handler
                if self._hook_dispatcher is not None:
                    self._hook_dispatcher.register(event, wrapped)
                committed_hooks.append((event, wrapped))
            self._governance_reducers.extend(self._pending_governance)
            committed_governance_count = len(self._pending_governance)
            self._committed = True
        except Exception:
            self._rollback(committed_tools, committed_hooks, committed_governance_count)
            raise

    def _rollback(self, tools, hooks, governance_count) -> None:
        for name in tools:
            if self._tool_registry is not None:
                try: self._tool_registry.unregister(name)
                except: pass
        for event, handler in hooks:
            if self._hook_dispatcher is not None:
                try: self._hook_dispatcher.unregister(event, handler)
                except: pass
        for _ in range(governance_count):
            if self._governance_reducers:
                self._governance_reducers.pop()


class DomainLifecycleContext:
    """生命周期注册上下文 — 注册路由、启动后台任务。"""

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
    def app(self) -> Any: return self._app
    @property
    def settings(self) -> Any: return self._settings

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
```

---

## 九、故障隔离

域工具/钩子执行异常不拖垮核心引擎。隔离层在 `DomainRuntimeContext.commit()` 中自动包裹（通过 `tool_wrapper` / `hook_wrapper` 注入），域开发者无感知。

```python
# prismclaw/engine/foundation/domain_isolation.py

class DomainIsolatedTool:
    """包裹域工具 — 异常转为 ToolResult(output=error_msg) 而非抛出。

    透传 Tool Protocol 的全部必选属性：
    name, description, parameters, side_effect, tool_class, effect_class
    """

    def __init__(self, tool: Any, domain_name: str) -> None:
        self._tool = tool
        self.domain = domain_name
        self.name = getattr(tool, "name", f"__domain_{domain_name}_unknown__")
        self.description = getattr(tool, "description", "")
        self.parameters = getattr(tool, "parameters", {})
        self.tool_class = getattr(tool, "tool_class", "write")
        self.side_effect = getattr(tool, "side_effect", True)
        self.effect_class = getattr(tool, "effect_class", "unknown")
        for attr in ("required", "annotations"):
            if hasattr(tool, attr):
                setattr(self, attr, getattr(tool, attr))

    async def execute(self, args: Any, context: Any) -> Any:
        try:
            return await self._tool.execute(args, context)
        except Exception as e:
            logger.exception("Domain '%s' tool '%s' failed", self.domain, self.name)
            from prismclaw.schema.content.tool_call import ToolResult
            return ToolResult(
                output=f"[{self.domain}] Error: {type(e).__name__}: {e}"
            )


class DomainIsolatedHook:
    """包裹域钩子 — 异常返回 HookResult(passed=True, error=...) 不阻断后续钩子。

    使用 execute() 方法名（与 HookHandler ABC 一致），
    通过运行时注册为 HookHandler 子类来通过 isinstance 严格检查。
    """

    def __init__(self, handler: Any, domain_name: str) -> None:
        self._handler = handler
        self.domain = domain_name
        for attr in ("name", "event"):
            if hasattr(handler, attr):
                setattr(self, attr, getattr(handler, attr))
        self._register_as_hook_handler()

    def _register_as_hook_handler(self) -> None:
        try:
            from prismclaw.engine.plugins.hooks.handlers import HookHandler
            HookHandler.register(DomainIsolatedHook)
        except ImportError:
            pass

    async def execute(self, context: Any) -> Any:
        try:
            if hasattr(self._handler, "execute"):
                result = await self._handler.execute(context)
            elif callable(self._handler):
                result = await self._handler(context)
            else:
                result = await self._handler(context)
            return result
        except Exception as e:
            logger.exception("Domain '%s' hook failed", self.domain)
            from prismclaw.engine.plugins.hooks.schema import HookResult
            return HookResult(passed=True, error=str(e))
```

治理 Reducer 是纯函数（不执行 IO），无需隔离。

---

## 十、DomainRegistry

发现、版本校验、依赖校验、编排。

```python
# prismclaw/engine/foundation/domain_registry.py

class DomainRegistry:
    """发现、加载、编排 DomainBundle。

    对标 Pi Agent 的 PackageManager + Harness 的 Cordis Plugin Loader。
    """

    ENTRY_POINT_GROUP = "prismclaw.domains"

    def __init__(self) -> None:
        self._bundles: dict[str, DomainBundle] = {}
        self._errors: dict[str, str] = {}

    def discover(self) -> list[DomainBundle]:
        """扫描 entry_points(group="prismclaw.domains")"""
        ...

    def load_enabled(self, enabled: list[str]) -> list[DomainBundle]:
        """按配置加载指定域，校验版本 + 依赖，返回按依赖拓扑排序后的域列表。"""
        discovered = {b.name: b for b in self.discover()}
        core_version = self._get_core_version()
        result: list[DomainBundle] = []
        for name in enabled:
            if name not in discovered:
                self._errors[name] = "not found"
                continue
            bundle = discovered[name]
            # 版本校验
            if bundle.required_core_version:
                from packaging.specifiers import SpecifierSet
                if core_version not in SpecifierSet(bundle.required_core_version):
                    self._errors[name] = f"requires core {bundle.required_core_version}, got {core_version}"
                    continue
            # 依赖校验
            missing_deps = [d for d in bundle.depends_on if d not in enabled]
            if missing_deps:
                self._errors[name] = f"depends on {missing_deps} which is not enabled"
                continue
            result.append(bundle)
            self._bundles[name] = bundle
        return result

    def apply_infrastructure(self, bundles, ctx: DomainInfrastructureContext) -> None:
        """基础设施阶段：注册持久化存储"""
        ...

    def apply_runtime(self, bundles, ctx_factory) -> None:
        """运行时阶段：注册工具、钩子、治理规则（事务性）"""
        for b in bundles:
            ctx = ctx_factory(b.name)
            try:
                b.on_build_runtime(ctx)
                ctx.commit()
            except Exception as e:
                logger.exception("Domain '%s' on_build_runtime failed", b.name)
                self._errors[b.name] = str(e)

    def apply_lifecycle(self, bundles, ctx: DomainLifecycleContext) -> None:
        """生命周期阶段：注册路由、启动后台任务"""
        ...

    @property
    def errors(self) -> dict[str, str]:
        """返回域加载错误（可用于 GET /api/domains/status）"""
        return dict(self._errors)
```

---

## 十一、接入启动流程

### 精确注入位置

| 阶段 | 位置 | 可用资源 |
|------|------|---------|
| Infrastructure | `apply_app_infrastructure_bundle()` 之后 | redis_client, db_engine, session_factory |
| Runtime | `assemble_runner_deps()` 内部、AgentRunnerDeps 构造之前 | ToolRegistry, HookRegistry |
| Lifecycle | `apply_app_lifecycle_bundle()` 之后、SPA mount 之前 | 全部 app.state |

### _lifespan.py

```python
async def _lifespan(app: FastAPI):
    # ... 阶段 A-E 不变 ...

    # Infrastructure
    infra_bundle = await build_app_infrastructure_bundle(...)
    apply_app_infrastructure_bundle(app, infra_bundle)

    # ▼ Domain Infrastructure
    domain_registry = DomainRegistry()
    domain_bundles = domain_registry.load_enabled(settings.domains.enabled)
    domain_registry.apply_infrastructure(
        domain_bundles,
        DomainInfrastructureContext(
            db_engine=app.state.db_engine,
            redis_client=infra_bundle.redis_client,
            session_factory=app.state.session_factory,
            settings=settings,
            app=app,
        ),
    )
    app.state.domain_bundles = domain_bundles
    app.state.domain_registry = domain_registry

    # Runtime（域注册在 assemble_runner_deps 内部执行）
    runtime_bundle = await build_app_runtime_bundle(...)
    apply_app_runtime_bundle(app, runtime_bundle)

    # Lifecycle
    lifecycle_bundle = await build_app_lifecycle_bundle(...)
    apply_app_lifecycle_bundle(app, lifecycle_bundle)

    # ▼ Domain Lifecycle
    domain_registry.apply_lifecycle(
        domain_bundles,
        DomainLifecycleContext(app=app, settings=settings),
    )

    # ... 阶段 I-L 不变 ...
    yield
```

### assemble_runner_deps 内部

```python
    # 内置工具注册
    register_builtin_tools(tools, resolver, capability_catalog, skill_runtime)
    register_tier2_builtin_tools(tools, resolver, ...)

    # ▼ Domain Runtime
    domain_governance_reducers: list[ToolGovernanceReducer] = []
    domain_registry = getattr(app.state, "domain_registry", None) if app else None
    domain_bundles = getattr(app.state, "domain_bundles", []) if app else []
    if domain_registry and domain_bundles:
        domain_registry.apply_runtime(
            domain_bundles,
            ctx_factory=lambda name: DomainRuntimeContext(
                domain_name=name,
                tool_registry=tools,
                hook_dispatcher=hook_dispatcher,
                governance_reducers=domain_governance_reducers,
                tool_wrapper=DomainIsolatedTool,
                hook_wrapper=DomainIsolatedHook,
            ),
        )

    # 治理（默认 7 个 + 域 Reducer）
    deps_governance = ToolGovernance(extra_reducers=domain_governance_reducers) if domain_governance_reducers else None

    deps = AgentRunnerDeps(
        ...,
        governance=deps_governance,
    )
```

### 治理链打通

| # | 改造 | 文件 |
|---|------|------|
| 1 | AgentRunnerDeps 新增 `governance: ToolGovernance \| None = field(default=None)` | `loop/deps.py` |
| 2 | ToolGovernance 新增 `extra_reducers` 参数：追加在默认 7 个之后 | `governance.py` |
| 3 | 4 个调用点传入 `governance=deps.governance` | `pre_request_pipeline.py` / `_loop_mixin.py` / `catalog.py` |

---

## 十二、域 Agent 会话机制

域的 REST API 入口通过 `create_domain_session()` 创建域 Agent 会话，复用系统 Agent 的完整执行能力。

### 接口

```python
# prismclaw/runtime/facade/chat.py

async def create_domain_session(
    domain: str,            # 域名标识，如 "medical" / "legal"
    message: str,           # 用户消息
    files: list[str] = [],  # 上传文件 ID 列表
    skill_hint: str = "",   # 技能提示，引导 Agent 使用特定域技能
) -> dict[str, Any]:
    """创建域 Agent 会话，执行后返回结果。

    内部流程：
    1. 根据 domain 查找域工具/技能/钩子/治理
    2. 构造 RunRequest（注入域技能上下文）
    3. 通过 ChatService.start_conversation() 启动 Agent 执行
    4. 收集 Agent 输出，返回结构化结果
    """
    ...
```

### 执行流程

```
REST API 入口
    │
    ▼
create_domain_session(domain, message, files, skill_hint)
    │
    ├─ 1. 查找域已注册的工具/技能/钩子/治理
    ├─ 2. 构造 RunRequest
    │       ├─ 注入 skill_hint → 引导 Agent 选用域技能
    │       └─ 携带 files → 传递文件上下文
    ├─ 3. ChatService.start_conversation() → Agent Loop
    │       ├─ Agent 识别意图 → 选择域工具调用
    │       ├─ 域钩子自动生效（如 PHI 脱敏）
    │       └─ 域治理规则自动生效（如 HIPAA Reducer）
    └─ 4. 收集 Agent 输出 → 返回结构化结果
```

### 双入口共享同一能力

```
                    ┌─────────────────────────────────┐
                    │         域工具 + 技能            │
                    │  (smart_medical_record, etc.)     │
                    └───────────┬─────────────────────┘
                                │
               ┌────────────────┼────────────────┐
               │                │                │
    create_domain_session()   Agent Loop       钩子/治理
        (页面 REST API)      (聊天直接调用)     (自动生效)
```

---

## 十三、前端动态化

### 现状

- Chat.tsx ~3000 行，侧边栏硬编码 4 项，内容区 if-else 链
- AppSection 编译时固定字面量联合类型
- useChatSocket.ts 的 default 分支已有 CustomEvent 总线（`scheduled_task.*` 消息）
- 非 Chat 模块已遵循 REST + CustomEvent + 自包含 store 模式

### 改造（总改动 < 150 行）

**1. AppSection 动态化**（`stores/ui.ts`）

```typescript
// 改前：type AppSection = 'chat' | 'experts' | 'tasks' | 'files' | 'settings' | 'outputs'
// 改后：
export type AppSection = string
```

**2. 模块注册表**（`lib/module-registry.ts`，新文件 ~80 行）

```typescript
interface ModuleDescriptor {
  key: string
  label: string
  icon: string
  order: number
  showInNav: boolean
  Content: ComponentType<ModuleContext>
  wsPrefix?: string
}

interface ModuleContext {
  token: string | null
  workspaceId: string
  navigate: (section: string, convId?: string) => void
  send: (msg: { type: string; [k: string]: unknown }) => void
  wsState: 'connecting' | 'identifying' | 'ready' | 'disconnected'
}

const moduleRegistry = new Map<string, ModuleDescriptor>()
export function registerModule(d: ModuleDescriptor): void { moduleRegistry.set(d.key, d) }
export function getModules(): ModuleDescriptor[] { return [...moduleRegistry.values()] }
export function getModule(key: string): ModuleDescriptor | undefined { return moduleRegistry.get(key) }
```

**3. 侧边栏 + 内容区**（`pages/Chat.tsx`）

```typescript
// 侧边栏：硬编码 → 注册表
{getModules().filter(m => m.showInNav).sort((a,b) => a.order - b.order)
  .map(({ key, label, icon }) => ...)}

// 内容区：if-else → 注册表
const activeModule = getModule(activeSection)
{activeModule ? <activeModule.Content {...commonProps} /> : null}
```

**4. WS 域消息路由**（`hooks/useChatSocket.ts`）

```typescript
// default 分支扩展
if (msg.type.startsWith('domain.')) {
  const moduleKey = msg.type.split('.')[1]
  window.dispatchEvent(new CustomEvent(`prismclaw:module:${moduleKey}:ws`, { detail: msg }))
}
```

**5. 域 Manifest API**（`app/web/routers/domains.py`）

```python
@router.get("/manifests")
async def get_domain_manifests(request: Request) -> list[dict[str, Any]]:
    """返回所有已加载域的前端描述（菜单、页面、图标）。"""
    bundles = getattr(request.app.state, "domain_bundles", None) or []
    manifests = []
    for bundle in bundles:
        try:
            manifest = bundle.get_manifest()
            manifests.append({
                "key": manifest.key,
                "label": manifest.label,
                "icon": manifest.icon,
                "path": manifest.path,
                "menus": [
                    {"key": m.key, "label": m.label, "icon": m.icon, "parent": m.parent}
                    for m in manifest.menus
                ],
                "pages": [
                    {"key": p.key, "path": p.path, "js_asset": p.js_asset, "css_asset": p.css_asset}
                    for p in manifest.pages
                ],
            })
        except Exception:
            pass
    return manifests
```

---

## 十四、样式规范（glass-card 设计系统）

所有域页面遵循项目 glass-card 设计系统：

| 元素 | 规范 |
|------|------|
| 容器 | `flex-1 overflow-hidden flex flex-col min-w-[950px]` |
| 内容宽度 | `max-w-[1200px] mx-auto` |
| 顶部栏 | `px-8 pt-7 pb-5 shrink-0` |
| 滚动区 | `flex-1 overflow-y-auto px-8 py-6` |
| 卡片 | `glass-card`（12px 圆角 + 磨砂模糊 + 噪点纹理 + 悬浮投影） |
| 主按钮 | `PrimaryButton`（`bg-[var(--c-btn-primary-bg)] text-[var(--c-btn-primary-fg)]`） |
| 次按钮 | `SecondaryButton`（透明底 + `border-[var(--c-border)]`） |
| 图标 | `lucide-react` |
| 颜色 | `var(--c-*)` 系统变量 |
| 字号 | `var(--fs-*)` 7 档音阶（micro 11px / caption 12px / small 13px / body 14px / ui 14px / heading 16px / title 20px） |
| 输入框 | `glass-input-field` + 内部 `<input>` / `<textarea>` |
| 跳转对话 | `setActiveSection('chat')` + `setPendingComposerPrefill({ text, nonce })` |
| 文件上传 | 拖拽区 `border-2 border-dashed border-[var(--c-border)]` + hover `border-[var(--c-accent)]` |
| 搜索结果 | `glass-card` 卡片列表 + hover 浮起 `translateY(-2px)` |
| 加载中 | `glow-pulse` 动画 + "AI 思考中..." 提示 |

工作台布局规范：

| 区域 | 样式 |
|------|------|
| 工作台容器 | `flex-1 overflow-hidden flex min-w-[950px]` |
| 左侧菜单栏 | `w-[200px] shrink-0 border-r border-[var(--c-border)] px-3 py-4 flex flex-col gap-1` |
| 菜单项 | `px-3 py-2.5 rounded-lg text-[var(--fs-body)] cursor-pointer`，选中 `bg-[var(--c-surface-2)] text-[var(--c-accent)]`，hover `bg-[var(--c-surface-2)]/50` |
| 右侧内容区 | `flex-1 overflow-y-auto`，承载各功能页面 |

---

## 十五、域模块规范

### 包结构

```
subpackages.medical/
  pyproject.toml
  src/subpackages.medical/
    bundle.py              # MedicalBundle
    tools/                 # 域工具
    hooks/                 # 域钩子
    governance/            # 域治理规则
    routes/                # 域 API
    persistence/           # 域数据存储
    skills/                # SKILL.md
  frontend/src/                 # 前端
    pages/                 # React 页面
    components/            # React 组件
    index.ts               # 模块注册导出
```

### MedicalBundle 示例

```python
class MedicalBundle:
    name = "medical"
    required_core_version = ">=0.1.0,<2.0.0"
    depends_on = []

    def get_manifest(self) -> DomainManifest:
        return DomainManifest(
            key="medical", label="医疗", icon="stethoscope",
            path="/medical",               # 工作台路由
            menus=[
                MenuEntry(key="record", label="智能病历", icon="file-text"),
                MenuEntry(key="translate", label="医学翻译", icon="languages"),
                MenuEntry(key="guideline", label="搜指南", icon="book-open"),
                MenuEntry(key="drug", label="搜药品", icon="pill"),
                MenuEntry(key="literature", label="搜文献", icon="search"),
            ],
            pages=[
                PageEntry(key="smart-record", path="/api/medical/assets/records.js"),
                PageEntry(key="translate", path="/api/medical/assets/translate.js"),
            ],
        )

    def on_build_infrastructure(self, ctx):
        store = MedicalStore(ctx.session_factory)
        ctx.register_store("medical", store)

    def on_build_runtime(self, ctx):
        ctx.register_tool(ParseRecordTool())
        ctx.register_tool(SearchKBTool())
        ctx.register_tool(DiagnosisTool())
        ctx.register_hook("PRE_TOOL", PhiRedactHandler())
        ctx.register_governance(HipaaReducer())

    def on_build_lifecycle(self, ctx):
        ctx.register_route(records_router, prefix="/api/medical")
        ctx.register_route(diagnosis_router, prefix="/api/medical")
```

### pyproject.toml

```toml
[project]
name = "prismclaw-medical"
version = "0.1.0"
dependencies = ["prismclaw>=0.1.0,<2.0.0", "packaging>=21.0"]

[project.entry-points."prismclaw.domains"]
medical = "subpackages.medical.integration.bundle:MedicalBundle"
```

### 数据层规范

| 规范 | 说明 |
|------|------|
| PG 表名 | `pc_{domain}_*`（如 `pc_medical_records`） |
| Redis 键 | `{prefix}{domain}:*`，prefix 从 `settings.redis.key_prefix` 派生 |
| DDL 安全 | `pg_advisory_xact_lock` + advisory lock key 在 742200+ 区间 |
| 禁止 | 不直接操作其他域的表 |

---

## 十六、域特化概述

### 医疗域（subpackages.medical）

面向**肿瘤与质子治疗**的临床智能化工作台。5 功能工作台模式，MenuEntry >= 2，左侧二级菜单 + 右侧功能页。

| 菜单名 | Tab Key | 工具名 | REST 端点 | Agent 执行内容 |
|---|---|---|---|---|
| 智能病历 | `record` | `smart_medical_record` | `POST /api/medical/records/organize` | 解析文件 → 结构化肿瘤病历 |
| 医学翻译 | `translate` | `medical_translate` | `POST /api/medical/translate` | 中英互译 + 肿瘤学术语对齐 |
| 搜指南 | `guideline` | `search_guideline` | `POST /api/medical/guideline/search` | 语义检索 NCCN/CSCO 指南 |
| 搜药品 | `drug` | `search_drug` | `POST /api/medical/drug/search` | 肿瘤药品信息 + 化疗方案模板 |
| 搜文献 | `literature` | `search_literature` | `POST /api/medical/literature/search` | 语义检索文献 + 年会追踪 |

路由：`/medical?tab={key}`

详见 → [medical-domain-design.md](../medical/medical-domain-design.md)

### 法律域（subpackages.legal）

面向**案件管理+跟踪+文书起草**的法律智能化工作台。3 功能工作台模式，MenuEntry >= 2，左侧二级菜单 + 右侧功能页。

| 菜单名 | Tab Key | 工具名 | REST 端点 | Agent 执行内容 |
|---|---|---|---|---|
| 案件管理 | `cases` | `case_manage` | `POST /api/legal/cases/manage` | 提取案件信息 → 结构化案件 |
| 案件跟踪 | `track` | `case_track` | `POST /api/legal/cases/track` | 分析进展 → 预测下一步 + 时间线 |
| 文书起草 | `draft` | `document_draft` | `POST /api/legal/documents/draft` | 模板 + 参数 → 法律文书 |

路由：`/legal?tab={key}`

详见 → [legal-domain-design.md](../legal/legal-domain-design.md)

---

## 十七、改造清单

### Phase 0：基础设施（零破坏新增）

| # | 改造 | 文件 |
|---|------|------|
| 0.1 | DomainBundle Protocol + DomainManifest + MenuEntry + PageEntry + 三个 Context | `schema/services/domain.py`（新） |
| 0.2 | DomainIsolatedTool + DomainIsolatedHook | `engine/foundation/domain_isolation.py`（新） |
| 0.3 | DomainRegistry | `engine/foundation/domain_registry.py`（新） |
| 0.4 | DomainSettings | `infra/settings.py` |
| 0.5 | Bundle 去掉 slots=True + 域字段 | `assembly/bundles_data.py` |

### Phase 1：治理链打通

| # | 改造 | 文件 |
|---|------|------|
| 1.1 | AgentRunnerDeps 新增 governance 字段 | `engine/execution/loop/deps.py` |
| 1.2 | ToolGovernance extra_reducers 追加语义 | `engine/toolkit/registry/governance.py` |
| 1.3 | 4 个调用点传入 governance | `pre_request_pipeline.py` / `_loop_mixin.py` / `catalog.py` |
| 1.4 | assemble_runner_deps 构造域 governance | `wiring/workspace/runner_assembly.py` |

### Phase 2：接入 assembly 层

| # | 改造 | 文件 |
|---|------|------|
| 2.1 | _lifespan infrastructure + lifecycle 接入 | `app/_lifespan.py` |
| 2.2 | assemble_runner_deps 域注册 | `assembly/workspace/runner_assembly.py` |
| 2.3 | SPA 延迟挂载 | `app/__init__.py` |
| 2.4 | 域 manifest + status API | `app/web/routers/domains.py`（新） |
| 2.5 | Office 硬绑定解耦 | `attachments.py` + `builtin.py` |

### Phase 3：前端动态化

| # | 改造 | 文件 |
|---|------|------|
| 3.1 | AppSection → string | `stores/ui.ts` |
| 3.2 | ModuleDescriptor 注册表 | `lib/module-registry.ts`（新） |
| 3.3 | 内置模块注册 | `modules/builtins.ts`（新） |
| 3.4 | 侧边栏 + 内容区注册表驱动 | `pages/Chat.tsx` |
| 3.5 | WS domain.* 消息路由 | `hooks/useChatSocket.ts` |

### Phase 4：SDK 断裂修复

| # | 改造 | 文件 |
|---|------|------|
| 4.1 | ExtensionManager 消费 native_tools | `extension_manager.py` |
| 4.2 | ToolDescriptor 类型 | `descriptors.py` |
| 4.3 | PluginRuntimeBundle tools 字段 | `plugin_lifecycle.py` |

### 工作量

| Phase | 新增文件 | 修改文件 | 新增行 | 修改行 |
|-------|---------|---------|-------|-------|
| 0 | 3 | 2 | ~400 | ~20 |
| 1 | 0 | 5 | ~30 | ~40 |
| 2 | 1 | 4 | ~80 | ~60 |
| 3 | 2 | 3 | ~180 | ~50 |
| 4 | 0 | 3 | ~30 | ~30 |
| **合计** | **6** | **17** | **~720** | **~200** |
