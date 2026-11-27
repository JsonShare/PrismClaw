# 业务域分包标准范本（demo）

> 适用：`backend/src/subpackages/<domain>` 与 `frontend/subpackages/<domain>`。
> 本文以 `demo` 为占位域名，给出目录结构、分层契约与实现规范；新增业务域时整体复制本范本，只替换域名与业务内容。现有两类落地形态可对照：`medical` 为完整参考实现，`legal` 为导航与页面骨架。
>
> 上位规范：[业务域功能架构规范](./domain-bundle-design.md)。范本只解决“怎么组织代码”，不改变“业务域不含独立 Agent / 专家 / 技能 / 插件”的定位。

---

## 一、范本边界

### 可直接建设

页面、业务 API、业务服务、业务规则、业务数据与持久化、域菜单声明、工作台装配。

### 不得出现在域包内

`agents/`、`experts/`、`skills/`、`commands/`、`plugins/`、`.plugin/`、`mcp.json`、独立模型配置、独立 Agent Loop、与核心并行的会话或任务状态存储。

### 保留的核心骨架

域包之外的 `DomainBundle` 协议、`DomainRegistry`、`/api/domains/manifests`、`frontend/src/domains/registry.ts`、`DomainSection` 属于核心，不得复制进域包。

---

## 二、目录结构

### 2.1 后端

```text
backend/src/subpackages/demo/
├── pyproject.toml              # 独立可安装包 + entry point 注册
├── README.md                   # 域职责、菜单、数据表、接口清单
├── __init__.py                 # 包说明，不含逻辑
├── _manifest_generated.py      # 编译产物，禁止手改
│
├── integration/                # 唯一与核心装配打交道的层
│   ├── __init__.py
│   └── bundle.py               # DemoBundle：三阶段注册 + get_manifest
│
├── api/                        # HTTP 层
│   ├── __init__.py
│   ├── deps.py                 # Store / 服务依赖获取
│   └── items.py                # 业务路由（按资源分文件）
│
├── models/                     # 请求 / 响应 / 视图模型
│   ├── __init__.py
│   ├── schemas.py              # 入参与校验
│   └── view_models.py          # 出参组装（camelCase 直出）
│
├── services/                   # 业务编排
│   ├── __init__.py
│   └── item_service.py         # 事务边界、状态流转、核心任务组装与结果校验
│
├── domain/                     # 纯业务规则，无 IO
│   ├── __init__.py
│   ├── enums.py                # 状态、类型枚举
│   └── rules.py                # 状态机、校验函数
│
├── persistence/                # 数据访问
│   ├── __init__.py
│   ├── store.py                # DemoStore
│   └── helpers.py              # get_demo_store / row_to_dict
│
└── deploy/
    └── sql/
        └── 001_init.sql        # 业务表 DDL，启动时自动扫描执行
```

### 2.2 前端

```text
frontend/subpackages/demo/
├── DemoWorkbenchPage.tsx       # 唯一入口，必须位于分包根目录
├── src/
│   ├── app/
│   │   └── DemoWorkbenchApp.tsx    # 页切换 + URL 同步 + 侧栏回报
│   ├── pages/
│   │   ├── OverviewPage.tsx
│   │   └── DetailPage.tsx
│   ├── components/                 # 域内私有组件
│   ├── api/
│   │   ├── client.ts               # 统一请求封装（token、错误归一）
│   │   └── demo.ts                 # 业务接口函数
│   ├── types/
│   │   └── index.ts                # 与后端 view model 对齐的类型
│   └── styles/
│       ├── demo.css                # 结构与令牌转发
│       ├── light.css               # [data-theme=light] 取值
│       └── dark.css                # [data-theme=dark] 取值
└── assets/
    └── icons/                      # 侧栏图标（构建期哈希）
```

前端不放 `node_modules` 之外的构建配置、不放独立路由库、不放全局主题覆盖。

---

## 三、装配契约

### 3.1 域名与入口

| 位置 | 键 | 值 |
|---|---|---|
| `scripts/compile/domains.yaml` | 域名 | `demo` |
| `pyproject.toml` entry point | `demo` | `subpackages.demo.integration.bundle:DemoBundle` |
| `domains.yaml` | `lazy_import` | `@domains/demo/DemoWorkbenchPage` |
| `domains.yaml` | `lazy_export` | `DemoWorkbenchPage` |

四个位置的域名与导出名必须一致，任一处不一致都会导致域被发现但页面加载失败。

### 3.2 domains.yaml 条目

```yaml
  demo:
    enabled: false            # 范本默认不参与构建
    label: 演示工作台
    backend_path: backend/src/subpackages/demo
    frontend_path: subpackages/demo
    lazy_import: "@domains/demo/DemoWorkbenchPage"
    lazy_export: DemoWorkbenchPage
    menus:
      - key: overview
        label: 概览
        group: 演示
        icon_asset: assets/icons/overview.svg
      - key: detail
        label: 明细
        group: 演示
        icon_asset: assets/icons/detail.svg
```

菜单的标签、顺序、分组只在此声明。禁止在 `_manifest_generated.py` 或前端手写第二份菜单。

### 3.3 编译与安装链路

```text
domains.yaml
  └─ node scripts/compile/domains-registry.mjs
       ├─ frontend/src/domains/_lazy_map.generated.ts   # 懒加载映射 + 导航项
       ├─ backend/src/subpackages/demo/_manifest_generated.py
       └─ scripts/compile/out/.domains.env              # PRISMCLAW_BACKEND_PATHS
  └─ pip install -e backend/src/subpackages/demo        # 仅启用的域
  └─ npm run build（frontend）
```

被裁剪（`enabled: false`）的域不生成前端映射；若后端仍返回该域 manifest，前端会跳过注册，不报错也不白屏。

---

## 四、后端分层契约

### 4.1 依赖方向

```text
api ──► services ──► domain
 │          │
 │          └──────► persistence ──► schema
 └────────────────────────────────────┘（只读 Store 接口）

integration ──► 以上全部（仅装配，不含业务逻辑）
```

硬性规则：

1. `domain/` 不导入 FastAPI、SQLAlchemy、`persistence`、`api`、`services`；只放纯函数与枚举。
2. `persistence/` 不导入 `api`、`services`；只做 SQL 与行映射。
3. `services/` 可调 `domain` 与 `persistence`，可调核心公开门面，但不 import `prismclaw.engine` 内部模块。
4. `api/` 只做鉴权、参数校验、调用服务、组装响应；不写 SQL、不写业务判断分支。
5. `integration/bundle.py` 只做注册与接线，不实现业务。

### 4.2 Bundle 骨架

```python
class DemoBundle:
    name = "demo"
    required_core_version = ">=0.1.0,<2.0.0"
    depends_on: list[str] = []
    task_store: Any | None = None

    async def on_build_infrastructure(self, ctx: DomainInfrastructureContext) -> None:
        from ..persistence.schema import ensure_demo_schema
        from ..persistence.store import DemoStore

        ctx.register_store("demo", DemoStore(ctx.db_engine))
        await ensure_demo_schema(ctx.db_engine)

    def on_build_runtime(self, ctx: DomainRuntimeContext) -> None:
        # 目标形态不注册域工具 / 域技能 / 域钩子
        ctx.commit()

    def on_build_lifecycle(self, ctx: DomainLifecycleContext) -> None:
        from ..api.items import router as items_router

        ctx.register_route(items_router, "/api/demo/items")

    def get_manifest(self) -> DomainManifest:
        from .._manifest_generated import MANIFEST

        return MANIFEST
```

要点：

- `on_build_infrastructure` 可以是 `async`，其余同步。
- `on_build_runtime` 必须调用 `ctx.commit()`，即使没有注册内容。
- Store 通过 `ctx.register_store(name, store)` 暴露，路由用 `AppState.get_domain_store` 取回，不建立模块级全局单例。
- `get_manifest` 只读编译产物，不手写菜单。

### 4.3 路由与依赖

```python
router = APIRouter(tags=["demo"])

@router.get("")
async def list_items(
    request: Request,
    store: DemoStore = Depends(get_demo_store),
) -> dict[str, Any]:
    ...
```

- 路由前缀由 `register_route` 的 `prefix` 决定，`APIRouter` 内不再重复域前缀。
- `deps.py` 统一提供 `get_demo_store`，签名即文档。
- 出参直接返回 camelCase 视图模型，前端不做字段搬运。

### 4.4 服务与状态流转

- 事务边界在 `services/`，一服务方法一次业务动作。
- 状态流转必须经 `domain/rules.py` 的显式函数校验，不在路由里写 `if status == ...`。
- 写操作记录 `created_by` / `updated_by`，关键动作写操作日志。
- 需要幂等的写接口接收 `client_request_id`，重复请求返回同一结果。

### 4.5 智能动作接入

```text
api（校验业务对象与参数）
  → services（组装最小上下文 + 输出契约）
  → 核心公开会话 / 任务应用服务（默认核心 Agent，按需 expert_id）
  → 返回结果或产出物引用
  → services 按 Schema 校验
  → persistence 写入业务结果
  → api 返回业务视图
```

- 业务服务只保存 `session_id` / `task_id` 关联，不复制核心运行状态。
- 模型输出未经校验不得落库。
- 执行者选择以 `expert_id` 表达，域包不保存专家人格或技能正文。

---

### 4.6 SQL 自建脚本

业务表 DDL 属于域自有资产，随域包存放，不进入仓库根 `deploy/sql/`（那里只保留核心 `init_postgres.sql`）。

| 项目 | 规范 |
|---|---|
| 位置 | `backend/src/subpackages/<domain>/deploy/sql/` |
| 命名 | `<3 位序号>_<描述>.sql`，如 `001_init.sql`；按文件名升序执行 |
| 触发 | 系统启动装配域后自动扫描，Bundle 不写任何 schema 调用 |
| 幂等 | 语句使用 `IF NOT EXISTS`；每个文件独立持有 advisory lock |
| 失败 | 记入 `/api/domains/status`，不阻断服务启动 |
| 无表 | 没有 `deploy/sql` 目录视为无业务表，正常跳过且不报错 |

约束：

- 域包不得自带 SQL 执行器、迁移框架或版本表；扫描与执行由核心 `apply_domain_sql` 统一实现。
- 表名、索引名仍按 `<pc_<domain>_*>` 命名；SQL 文件只描述结构，不写入业务数据。
- 变更已有脚本仅限未上线阶段；上线后新增改动一律追加新序号脚本，不改历史文件。

## 五、前端实现规范

### 5.1 入口文件

```tsx
export interface DemoWorkbenchPageProps {
  page?: string
  pageNonce?: number
}

export function DemoWorkbenchPage({ page, pageNonce }: DemoWorkbenchPageProps) {
  return <DemoWorkbenchApp page={page} pageNonce={pageNonce} />
}

export default DemoWorkbenchPage
```

- 文件必须保持在分包根目录，不可移入 `src/`。
- 导出名必须与 `domains.yaml` 的 `lazy_export` 完全一致。
- 样式只在入口导入一次，避免重复注入。

### 5.2 页切换与 URL

- 侧栏下发的 `page` 是唯一上行意图；`pageNonce` 自增表示“重复点击同一项”。
- 域内切页后调用 `reportDomainPage('demo', key)` 回报，供侧栏高亮。
- URL 统一写 `?domain=demo&page=<key>`，刷新与深链不丢当前页。
- 切换域时清理其他域的详情参数，避免被误读为有效详情 ID。

### 5.3 组件与样式

- 使用系统主题变量 `var(--c-*)`、字号 `var(--fs-*)` 与共享按钮、输入框、卡片；不新建全局主题。
- 域内样式按 `demo.css`（结构）+ `light.css` / `dark.css`（取值）三分组织。
- 图标使用 `lucide-react`，侧栏图标放 `assets/icons/` 由构建期哈希。
- 加载态、空态、错误态必须显式实现，不用通用 toast 掩盖业务失败。

### 5.4 数据访问

- 所有请求经 `api/client.ts`，统一附带 token、归一错误结构。
- 类型定义放 `types/index.ts`，与后端 view model 字段一一对应。
- 不在组件内直接 `fetch`，不硬编码 `/api/demo` 之外的地址。

---

## 六、命名与数据规范

| 对象 | 规则 | 示例 |
|---|---|---|
| 域名 | 小写单词，无连字符 | `demo`、`medical`、`legal` |
| Python 类 | `<Domain><Role>` | `DemoBundle`、`DemoStore`、`DemoItemService` |
| 路由前缀 | `/api/<domain>/<resource>` | `/api/demo/items` |
| 数据表 | `pc_<domain>_<entity>` | `pc_demo_items` |
| 前端页面 | `<Domain><Name>Page.tsx` | `DemoOverviewPage.tsx` |
| 类型 | camelCase，与视图模型一致 | `itemId`、`createdAt` |
| 错误码 | `<domain>.<resource>.<reason>` | `demo.item.not_found` |
| SQL 脚本 | `<3 位序号>_<描述>.sql`，位于域包 `deploy/sql/` | `001_init.sql`、`002_add_index.sql` |

数据规范：

- DDL 由域包 `deploy/sql/*.sql` 持有，由核心启动时自动扫描执行；语句保持 `IF NOT EXISTS` 幂等，重复启动不报错。
- 域不读写其他域的表；跨域数据通过对方公开接口或核心能力获取。
- 单表主键优先使用可读短码或 UUID，与既有域保持一致。
- 时间统一存 UTC，出参按视图模型约定格式化。

---

## 七、错误与可观测

| 场景 | 处理 |
|---|---|
| 参数不合法 | 业务 API 返回 4xx 与错误码，不启动核心任务 |
| 业务对象不存在 | 返回 404 与错误码，不静默返回空结构 |
| 核心任务失败 | 保留业务数据，返回核心错误摘要，允许重试 |
| 结果校验失败 | 业务记录标记待处理，保留核心结果引用 |
| 重复提交 | 按 `client_request_id` 返回同一结果 |

日志要求：业务动作记录 `domain`、`action`、`target_id`、`operator`、`core_session_id`（如涉及核心任务）。禁止在日志中输出完整病历、当事人身份信息等敏感原文。

---

## 八、禁止清单

- 目录：`agents/`、`experts/`、`skills/`、`commands/`、`plugins/`、`.plugin/`、`mcp.json`。
- 代码：独立 Agent Loop、模型客户端封装、Provider 配置、域级工具注册、域级技能注入、域级治理 Reducer。
- 数据：与核心并行的会话表、消息表、运行状态表。
- 接口：`/api/<domain>/agent`、`/api/<domain>/skills`、`/api/<domain>/experts`。
- 前端：独立聊天应用、专家主页、专家团介绍页、与系统侧栏平行的自有导航。

---

## 九、范本自检清单

- [ ] 四个位置的域名与导出名一致（`domains.yaml` 域名、entry point、`lazy_import`、`lazy_export`）。
- [ ] `_manifest_generated.py` 由编译脚本生成，未被手改。
- [ ] `on_build_runtime` 调用了 `ctx.commit()`。
- [ ] Store 经 `ctx.register_store` 注册，路由经 `AppState.get_domain_store` 获取。
- [ ] 路由前缀只在 `register_route` 出现一次。
- [ ] `domain/` 无 IO 依赖，`persistence/` 不反向依赖 `services`。
- [ ] 状态流转集中在 `domain/rules.py`。
- [ ] 写接口具备 `client_request_id` 幂等处理。
- [ ] 业务表 DDL 位于域包 `deploy/sql/`，文件名带序号前缀且语句幂等。
- [ ] `on_build_infrastructure` 只注册 Store，未调用任何 schema / DDL 函数。
- [ ] 前端入口仍在分包根目录，样式只导入一次。
- [ ] 页切换同时更新 URL 并调用 `reportDomainPage`。
- [ ] 无第八节禁止清单中的任何目录或接口。
- [ ] `enabled: false` 时构建不报错，`enabled: true` 时菜单与页面正常出现。

---

## 十、从范本派生新域

1. 复制 `backend/src/subpackages/demo` → `backend/src/subpackages/<domain>`，全局替换 `demo` / `Demo` / `DEMO`。
2. 复制 `frontend/subpackages/demo` → `frontend/subpackages/<domain>`，同步替换导出的组件名。
3. 修改 `pyproject.toml` 的包名与 entry point。
4. 在域包内建 `deploy/sql/`，首个脚本命名 `001_init.sql`，写入幂等的业务表 DDL。
5. 在 `domains.yaml` 增加条目，声明菜单、分组、图标与启用状态。
6. 执行 `node scripts/compile/domains-registry.mjs`，确认三份产物均已生成。
7. `pip install -e backend/src/subpackages/<domain>`，重启服务验证菜单、页面与建表结果。
8. 按第九节清单自检，再开始写业务。

---

## 十一、待确认

- `demo` 是否需要落成可运行骨架（含一条示例资源的建表、列表、详情、状态流转），还是仅作为文档范本存在。
- 是否将“操作日志”“幂等键”抽为核心共享能力，避免每个域重复实现。
