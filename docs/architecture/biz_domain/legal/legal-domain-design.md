# 法律域（subpackages.legal）设计文档

> 本文档是 domain-bundle-design.md 的域特化展开。
> 通用机制（执行模型、DomainBundle Protocol、故障隔离、样式规范等）参见总纲，本文档只描述法律域的专属设计。

---

## 一、功能设计

### 1.1 功能总览

| 菜单名 | 工作台 Tab | 工具名 | REST 端点 | Agent 执行内容 |
|---|---|---|---|---|
| 案件管理 | `?tab=cases` | `case_manage` | `POST /api/legal/cases/manage` | 提取案件信息 → 结构化案件（当事人/案由/管辖/阶段） |
| 案件跟踪 | `?tab=track` | `case_track` | `POST /api/legal/cases/track` | 分析进展 → 预测下一步 + 提醒 + 时间线构建 |
| 文书起草 | `?tab=draft` | `document_draft` | `POST /api/legal/documents/draft` | 模板 + 参数 → 法律文书（起诉状/答辩状/合同等） |

> 所有功能在法律工作台 `/legal` 内以 Tab 切换，不平铺子路由。

### 1.2 REST API 请求/响应格式

#### 1.2.1 案件管理

**POST /api/legal/cases/manage**

```jsonc
// ── 请求：新建案件 ──
{
  "action": "create",
  "case_data": {
    "case_number": "(2025)京0105民初1234号",
    "case_type": "合同纠纷",           // 合同纠纷/侵权责任/劳动争议/知识产权/...
    "court": "北京市朝阳区人民法院",
    "description": "张三诉李四合同违约纠纷",
    "parties": [
      { "name": "张三", "role": "plaintiff", "contact": "13800138000" },
      { "name": "李四", "role": "defendant", "contact": "13900139000" },
      { "name": "王律师", "role": "agent_plaintiff", "contact": "lawyer@firm.com" }
    ],
    "files": []                        // 上传文件 ID 列表
  },
  "skill_hint": "案件管理"
}

// ── 请求：查询/更新/归档 ──
{
  "action": "query" | "update" | "archive",
  "case_id": "uuid-string",
  "case_data": { ... },               // update 时提供变更字段
  "filters": {                         // query 时提供筛选条件
    "status": "active" | "archived" | "all",
    "case_type": "合同纠纷",
    "keyword": "张三"
  },
  "skill_hint": "案件管理"
}

// ── 响应 ──
{
  "ok": true,
  "action": "create",
  "case": {
    "id": "uuid-string",
    "case_number": "(2025)京0105民初1234号",
    "case_type": "合同纠纷",
    "court": "北京市朝阳区人民法院",
    "status": "active",
    "description": "张三诉李四合同违约纠纷",
    "parties": [
      { "id": "uuid-p1", "name": "张三", "role": "plaintiff", "contact": "13800138000" },
      { "id": "uuid-p2", "name": "李四", "role": "defendant", "contact": "13900139000" },
      { "id": "uuid-p3", "name": "王律师", "role": "agent_plaintiff", "contact": "lawyer@firm.com" }
    ],
    "created_at": "2025-03-01T10:00:00Z",
    "updated_at": "2025-03-01T10:00:00Z"
  },
  "agent_result": {                    // Agent 执行的额外分析（如有）
    "summary": "本案为合同违约纠纷...",
    "risk_flags": ["被告可能提起反诉"],
    "suggested_next_steps": ["补充合同原件证据", "准备起诉状"]
  }
}
```

**领域知识要点**

| 子功能 | 关键设计结论 |
|--------|------------|
| **利益冲突检查** | 当事人ID统一管理：自然人以身份证号为唯一标识，法人以统一社会信用代码为唯一标识；维护关联关系图谱（投资/担保/亲属），支持多跳查询；冲突检查需覆盖历史案件（已结案仍需检查前客户冲突） |
| **诉讼时效计算** | 普通时效3年（自知道权利受损及义务人之日起），最长保护期20年（自权利受损之日起），取较早者；中断事由（催款/承诺还款/起诉）重置3年；中止事由（最后6个月内不可抗力等）暂停计算、恢复后加6个月；集成 chinese-calendar 处理节假日顺延 |
| **管辖权确定** | 四层判断：①专属管辖（不动产/港口/继承）→ ②协议管辖（须有效书面协议，不得违反级别/专属管辖）→ ③特殊地域管辖（按纠纷类型适用）→ ④一般地域管辖（被告住所地）；结合级别管辖标的额确定法院层级（基层/中级/高级/最高） |
| **诉讼费计算** | 财产案件按标的额阶梯费率速算（10级费率表）；简易程序/调解结案/撤诉均减半收取；非财产案件（离婚50-300元、劳动争议10元等）固定标准；申请费（执行费/保全费）另计 |

#### 1.2.2 案件跟踪

**POST /api/legal/cases/track**

```jsonc
// ── 请求 ──
{
  "action": "analyze" | "add_event" | "timeline",
  "case_id": "uuid-string",
  "event": {                           // add_event 时提供
    "event_type": "hearing" | "filing" | "evidence_exchange" | "judgment" | "deadline" | "other",
    "event_date": "2025-04-15",
    "title": "第二次开庭",
    "description": "质证环节",
    "is_predicted": false              // true 表示 AI 预测的未来事件
  },
  "skill_hint": "案件跟踪"
}

// ── 响应 ──
{
  "ok": true,
  "action": "analyze",
  "timeline": [
    {
      "id": "uuid-e1",
      "case_id": "uuid-string",
      "event_type": "filing",
      "event_date": "2025-03-01",
      "title": "立案",
      "description": "提交起诉材料，法院受理",
      "is_predicted": false,
      "created_at": "2025-03-01T09:00:00Z"
    },
    {
      "id": "uuid-e2",
      "case_type": "evidence_exchange",
      "event_date": "2025-03-15",
      "title": "证据交换",
      "description": "双方提交证据清单",
      "is_predicted": false,
      "created_at": "2025-03-15T14:00:00Z"
    },
    {
      "id": "uuid-e3",
      "event_type": "deadline",
      "event_date": "2025-04-15",
      "title": "第二次开庭",
      "description": "质证环节",
      "is_predicted": false,
      "created_at": "2025-04-01T10:00:00Z"
    },
    {
      "id": "uuid-e4",
      "event_type": "judgment",
      "event_date": "2025-05-01",
      "title": "判决（预计）",
      "description": "AI 根据案件进度预测",
      "is_predicted": true,
      "created_at": "2025-04-01T10:00:00Z"
    }
  ],
  "ai_analysis": {
    "current_stage": "审理中",
    "next_steps": [
      "4月15日前补充合同原件证据",
      "关注被告可能提起的反诉"
    ],
    "risk_flags": ["距下次开庭仅14天", "关键证据尚未完善"],
    "estimated_resolution": "2025年5月"
  }
}
```

**领域知识要点**

| 子功能 | 关键设计结论 |
|--------|------------|
| **程序阶段** | 一审普通程序审限6个月、简易程序3个月、小额诉讼2个月（一审终审）；二审审限3个月（判决）/30日（裁定）；再审申请期限6个月；执行申请期限2年 |
| **法定期限** | 答辩期15日、上诉期15日（判决）/10日（裁定）、举证期限普通程序不少于15日/简易程序不少于7日、诉前保全后30日内须起诉 |
| **节假日顺延** | 期间起算日+1天（开始的日不计入）；届满最后一日为法定节假日/周末则顺延至下一工作日；节假日在期间中间的不扣除；集成 chinese-calendar 判断工作日 |

#### 1.2.3 文书起草

**POST /api/legal/documents/draft**

```jsonc
// ── 请求 ──
{
  "action": "draft" | "refine" | "list_templates",
  "case_id": "uuid-string",            // 可选，关联案件
  "template": "民事起诉状",             // 文书模板名称
  "params": {                          // 填充参数
    "claims": "1. 判令被告支付违约金50万元；2. 判令被告承担诉讼费用",
    "facts_and_reasons": "原被告于2024年1月签订《采购合同》...",
    "evidence_list": "1. 《采购合同》原件；2. 付款凭证；3. 往来邮件"
  },
  "draft_id": "uuid-string",           // refine 时提供
  "refine_instruction": "在诉讼请求中增加利息部分",
  "skill_hint": "文书起草"
}

// ── 响应 ──
{
  "ok": true,
  "action": "draft",
  "document": {
    "id": "uuid-string",
    "case_id": "uuid-string",
    "template": "民事起诉状",
    "title": "民事起诉状",
    "content": "民事起诉状\n\n原告：张三...\n被告：李四...\n\n诉讼请求：\n1. 判令被告支付违约金50万元\n2. 判令被告承担诉讼费用\n\n事实与理由：\n原被告于2024年1月签订《采购合同》...\n\n此致\n北京市朝阳区人民法院\n\n具状人：张三\n日期：2025年3月1日",
    "version": 1,
    "status": "draft",
    "created_at": "2025-03-01T10:00:00Z"
  }
}
```

**领域知识要点**

| 子功能 | 关键设计结论 |
|--------|------------|
| **文书模板** | 2025年最高法67类示范文本（法〔2025〕82号），涵盖9大领域；采用要素式+表格化格式，新增纠纷解决意愿栏、法律术语解释栏 |
| **核心文书格式要素** | 标题（居中）→ 当事人信息（自然人：姓名/性别/出生日期/民族/住所/身份证号/联系方式；法人：名称/住所/法定代表人）→ 诉讼请求（编号列出、具体可执行）→ 事实与理由（含法条引用）→ 证据清单（编号+名称+类型+证明目的）→ 落款（此致+法院名称+签名+日期+附件） |
| **法条引用规范** | 首次出现用全称，后续可用括注简称；层级：条（汉字数字）→ 款（第X款）→ 项（汉字数字加括号）→ 目（阿拉伯数字）；并列引用顺序：法律及法律解释 > 行政法规 > 地方性法规 > 司法解释；先实体法后程序法 |
| **脱敏规则** | 身份证号：保留前6后4，中间8位用\*；姓名：保留姓氏，名字用×；手机号：保留前3后4，中间用\*；银行账号：保留前4后4；住址：保留至区/街道，门牌号用\* |

### 1.3 Agent 执行内容

各端点按总纲执行模型调用 `create_domain_session("legal", ...)`，注入法律域技能后执行：

| 功能 | Agent 注入技能 | Agent 执行流程 |
|---|---|---|
| 案件管理 | `案件管理.SKILL.md` | 1. 解析用户输入的案件信息<br>2. 调用 `case_manage` 工具写入数据库<br>3. 分析案件类型/风险，给出建议<br>4. 返回结构化案件 + Agent 分析结果 |
| 案件跟踪 | `案件跟踪.SKILL.md` | 1. 查询案件时间线<br>2. 分析当前阶段和进展<br>3. 预测下一步/风险/截止日<br>4. 返回时间线 + AI 分析 |
| 文书起草 | `文书起草.SKILL.md` | 1. 加载文书模板<br>2. 提取案件/当事人信息填充模板<br>3. 根据用户补充参数生成文书<br>4. 返回完整文书 |

### 1.4 legal-chineselaw 插件能力映射表

legal-chineselaw 已有 **12 个子域插件、20+ 命令、7 个工作流**，LegalBundle 只需**包装现有能力 + 提供页面入口**，无需自建工具/技能/MCP。

#### 子域 → 功能映射

| 子域插件 | 命令数 | 工作流数 | 映射到功能 | 对应命令（举例） |
|---|---|---|---|---|
| `litigation-legal` | 18 | 5 | 案件管理 + 案件跟踪 | `matter-intake`, `matter-update`, `chronology`, `portfolio-status`, `claim-chart` |
| `commercial-legal` | 7 | 4 | 文书起草（合同审阅类） | `review`, `amendment-history`, `escalation-flagger` |
| `corporate-legal` | 9 | 5 | 案件管理（尽调/交割类） | `diligence-issue-extraction`, `closing-checklist`, `entity-compliance` |
| `employment-legal` | 15 | 7 | 案件管理 + 文书起草 | `termination-review`, `hiring-review`, `policy-drafting`, `investigation-open` |
| `ip-legal` | 11 | 4 | 案件管理 + 文书起草 | `cease-desist`, `takedown`, `fto-triage`, `portfolio` |
| `privacy-legal` | 7 | 2 | 案件管理（合规类） | `dpa-review`, `dsar-response`, `pia-generation` |
| `regulatory-legal` | 7 | 3 | 案件跟踪（监管监控类） | `reg-feed-watcher`, `policy-diff`, `gaps` |
| `ai-governance-legal` | 8 | 1 | 案件管理（AI 治理类） | `use-case-triage`, `aia-generation`, `vendor-ai-review` |
| `product-legal` | 5 | 2 | 案件跟踪（发布审阅类） | `launch-review`, `marketing-claims-review`, `is-this-a-problem` |
| `legal-clinic` | 13 | 4 | 案件管理 + 文书起草 | `client-intake`, `memo`, `draft`, `deadlines` |
| `law-student` | 12 | 2 | — （学习辅助，不映射到三大功能） | — |
| `legal-builder-hub` | 8 | 2 | — （生态工具，不映射到三大功能） | — |

#### 命令 → REST 端点映射

| legal-chineselaw 命令 | 所属子域 | 映射到 REST 端点 | 说明 |
|---|---|---|---|
| `/litigation-legal:matter-intake` | litigation | `POST /api/legal/cases/manage {action:"create"}` | 案件接收，写入案件+当事人 |
| `/litigation-legal:matter-update` | litigation | `POST /api/legal/cases/track {action:"add_event"}` | 事项更新，追加时间线事件 |
| `/litigation-legal:chronology` | litigation | `POST /api/legal/cases/track {action:"timeline"}` | 时间线构建 |
| `/litigation-legal:matter-briefing` | litigation | `POST /api/legal/cases/track {action:"analyze"}` | 案件深度简报 |
| `/litigation-legal:demand-draft` | litigation | `POST /api/legal/documents/draft {template:"催告函"}` | 催告函起草 |
| `/litigation-legal:brief-section-drafter` | litigation | `POST /api/legal/documents/draft {template:"诉讼简报"}` | 简报章节起草 |
| `/corporate-legal:closing-checklist` | corporate | `POST /api/legal/cases/track {action:"add_event"}` | 交割清单 → 时间线事件 |
| `/employment-legal:policy-drafting` | employment | `POST /api/legal/documents/draft {template:"劳动政策"}` | 政策起草 |
| `/ip-legal:cease-desist` | ip | `POST /api/legal/documents/draft {template:"停止侵权函"}` | C&D 起草 |
| `/privacy-legal:dsar-response` | privacy | `POST /api/legal/documents/draft {template:"DSAR回复"}` | DSAR 响应起草 |
| `/legal-clinic:client-intake` | clinic | `POST /api/legal/cases/manage {action:"create"}` | 诊所接案 → 新建案件 |
| `/legal-clinic:draft` | clinic | `POST /api/legal/documents/draft` | 诊所文件初稿 |
| `/regulatory-legal:reg-feed-watcher` | regulatory | `POST /api/legal/cases/track {action:"analyze"}` | 监管动态 → 跟踪分析 |

> **关键结论**：LegalBundle 的 `on_build_runtime` 为空——所有工具/技能/MCP 已由 legal-chineselaw 插件注册。DomainBundle 只需提供数据库 Schema、REST 路由和前端页面入口。

---

## 二、UI 设计

### 2.1 侧边栏与工作台入口

启用法律域后，侧边栏新增**一级入口**，点击进入法律工作台：

```
┌──────────┐
│ 新任务    │  ← 固定
│ 能力扩展  │  ← 固定
│ 自动化    │  ← 固定
│ 产出物    │  ← 固定
│──────────│
│ ⚖️ 法律   │  ← 点击进入法律工作台（非展开子菜单）
└──────────┘
```

### 2.2 法律工作台页面（LegalWorkbenchPage）

```
┌──────────────────────────────────────────────────────────────────┐
│  ⚖️ 法律工作台                                                    │
│  ─────────────────────────────────────────────────────────────── │
│                                                                  │
│  ┌──────────┐  ┌──────────────────────────────────────────────┐  │
│  │          │  │                                              │  │
│  │ 案件管理  │  │  ← 当前选中的功能页面内容                      │  │
│  │ 案件跟踪  │  │     默认展示"案件管理"                         │  │
│  │ 文书起草  │  │                                              │  │
│  │          │  │                                              │  │
│  └──────────┘  └──────────────────────────────────────────────┘  │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

**布局规范**

| 区域 | 样式 |
|---|---|
| 工作台容器 | `flex-1 overflow-hidden flex min-w-[950px]` |
| 左侧菜单栏 | `w-[200px] shrink-0 border-r border-[var(--c-border)] px-3 py-4 flex flex-col gap-1` |
| 菜单项 | `px-3 py-2.5 rounded-lg text-[var(--fs-body)] cursor-pointer`，选中 `bg-[var(--c-surface-2)] text-[var(--c-accent)]`，hover `bg-[var(--c-surface-2)]/50` |
| 右侧内容区 | `flex-1 overflow-y-auto`，承载各功能页面 |

**路由设计**

| 路由 | 说明 |
|---|---|
| `/legal` | 工作台入口，默认显示案件管理 |
| `/legal?tab=track` | 工作台，显示案件跟踪 |
| `/legal?tab=draft` | 工作台，显示文书起草 |

### 2.3 设计规范

> 通用样式规范（glass-card 设计系统、字号音阶、按钮体系等）参见总纲，以下各节只标注法律域特有样式。

### 2.4 案件管理页面

#### 布局设计

```
┌──────────────────────────────────────────────────────────┐
│  案件管理                                                 │
│  AI 辅助创建和管理案件                                     │
│  ─────────────────────────────────────────────────────── │
│                                                          │
│  [＋ 新建案件]                        🔍 搜索案件...      │
│                                                          │
│  筛选：[全部] [进行中] [已归档]    类型：[全部▾]           │
│                                                          │
│  ┌──────────────────────────────────────────────────┐    │
│  │  📋 张三 vs 李四 · 合同纠纷                        │    │
│  │  案号：(2025)京0105民初1234号                       │    │
│  │  原告：张三    被告：李四    代理人：王律师           │    │
│  │  状态：进行中 · 下次开庭：2025-04-15                 │    │
│  │  [详情]  [跟踪]  [起草文书]                          │    │
│  └──────────────────────────────────────────────────┘    │
│  ┌──────────────────────────────────────────────────┐    │
│  │  📋 王五交通事故 · 侵权责任                         │    │
│  │  案号：(2025)京0108民初5678号                       │    │
│  │  原告：王五    被告：赵六                             │    │
│  │  状态：已归档 · 判决日期：2025-01-20                 │    │
│  │  [详情]  [跟踪]  [起草文书]                          │    │
│  └──────────────────────────────────────────────────┘    │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

#### 组件设计

| 组件 | 说明 | 样式 |
|---|---|---|
| `PageHeader` | 页面标题 + 描述 | `px-8 pt-7 pb-5 shrink-0`，标题 `text-[var(--fs-title)] font-semibold`，描述 `text-[var(--fs-body)] text-[var(--c-text-secondary)]` |
| `NewCaseButton` | 新建案件主按钮 | `PrimaryButton` + `Plus` icon（lucide-react） |
| `SearchInput` | 搜索框 | `glass-input-field` + `Search` icon 前缀 |
| `FilterTabs` | 状态筛选标签 | `SecondaryButton` 组，选中态 `bg-[var(--c-surface-2)] border-[var(--c-accent)]` |
| `CaseCard` | 案件卡片 | `glass-card p-5`，hover 时 `translateY(-2px)` + 边框增亮 |
| `CasePartyTag` | 当事人标签 | `inline-flex px-2 py-0.5 rounded text-[var(--fs-caption)]`，角色色标：原告蓝/被告红/代理人灰 |

#### 交互流程

1. **新建案件**：点击「＋ 新建案件」→ 弹出 `glass-card` 模态框 → 填写案号/案由/法院/当事人 → 点击「创建」→ `POST /api/legal/cases/manage {action:"create"}` → 列表刷新
2. **搜索**：输入关键词 → 防抖 300ms → `POST /api/legal/cases/manage {action:"query"}` → 列表过滤
3. **详情**：点击「详情」→ 展开卡片内嵌详情区 / 或跳转对话区 `setPendingComposerPrefill("请分析案件 xxx 的详情")`
4. **跟踪/起草**：点击「跟踪」→ 导航到 `/legal/track?case_id=xxx`；点击「起草文书」→ 导航到 `/legal/draft?case_id=xxx`

### 2.5 案件跟踪页面

#### 布局设计

```
┌──────────────────────────────────────────────────────────┐
│  案件跟踪                                                 │
│  AI 分析案件进展与下一步建议                                │
│  ─────────────────────────────────────────────────────── │
│                                                          │
│  选择案件：[张三 vs 李四 ▾]                                │
│                                                          │
│  ┌──────────────────────────────────────────────────┐    │
│  │  时间线                                            │    │
│  │                                                    │    │
│  │  ● 2025-03-01  立案                    已完成       │    │
│  │  │                                                 │    │
│  │  ● 2025-03-15  证据交换                已完成       │    │
│  │  │                                                 │    │
│  │  ● 2025-04-01  第一次开庭              已完成       │    │
│  │  │                                                 │    │
│  │  ○ 2025-04-15  第二次开庭（待办）        即将        │    │
│  │  │                                                 │    │
│  │  ◇ 2025-05-01  判决（AI 预测）          预计        │    │
│  │                                                    │    │
│  └──────────────────────────────────────────────────┘    │
│                                                          │
│  ── AI 建议 ──────────────────────────────────────────    │
│  ┌──────────────────────────────────────────────────┐    │
│  │  基于当前进展，建议：                               │    │
│  │  1. 4月15日前补充XX证据                             │    │
│  │  2. 关注被告可能提起的反诉                           │    │
│  │                                                    │    │
│  │  风险标记：                                         │    │
│  │  ⚠ 距下次开庭仅14天                                │    │
│  │  ⚠ 关键证据尚未完善                                │    │
│  └──────────────────────────────────────────────────┘    │
│                                                          │
│  [＋ 添加事件]  [发送到对话]                               │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

#### 组件设计

| 组件 | 说明 | 样式 |
|---|---|---|
| `CaseSelector` | 案件下拉选择器 | `glass-input-field` + `ChevronDown` icon，加载 `GET /api/legal/cases/manage` |
| `TimelineView` | 时间线视图 | 竖向 `flex flex-col`，每个事件节点 `flex items-start gap-3` |
| `TimelineNode` | 时间线节点 | 已完成：`●` 实心圆 `bg-[var(--c-accent)]`；待办：`○` 空心圆 `border-[var(--c-accent)]`；预测：`◇` 菱形 `border-[var(--c-text-secondary)]` |
| `TimelineConnector` | 节点间连线 | `w-px h-8 bg-[var(--c-border)]` ml-[6px] |
| `AISuggestionCard` | AI 建议卡片 | `glass-card p-4`，建议列表 `text-[var(--fs-body)]`，风险标记 `text-[var(--c-error)]` |
| `AddEventButton` | 添加事件按钮 | `SecondaryButton` + `Plus` icon |

#### 交互流程

1. **选择案件**：下拉选择 → `POST /api/legal/cases/track {action:"timeline"}` → 渲染时间线
2. **添加事件**：点击「添加事件」→ 弹出模态框填写日期/类型/描述 → `POST /api/legal/cases/track {action:"add_event"}` → 时间线刷新
3. **AI 分析**：点击时间线区标题或自动触发 → `POST /api/legal/cases/track {action:"analyze"}` → 渲染建议卡片
4. **发送到对话**：点击「发送到对话」→ `setActiveSection('chat')` + `setPendingComposerPrefill("请分析案件 xxx 的进展")`

### 2.6 文书起草页面

#### 布局设计

```
┌──────────────────────────────────────────────────────────┐
│  文书起草                                                 │
│  AI 根据模板和案件信息生成法律文书                           │
│  ─────────────────────────────────────────────────────── │
│                                                          │
│  关联案件：[张三 vs 李四 ▾]                                │
│  文书模板：[民事起诉状 ▾]                                  │
│                                                          │
│  ┌──────────────────────────────────────────────────┐    │
│  │  补充信息（可选）：                                 │    │
│  │                                                    │    │
│  │  诉讼请求：___________________________             │    │
│  │  事实与理由：_________________________             │    │
│  │  证据清单：___________________________             │    │
│  │                                                    │    │
│  └──────────────────────────────────────────────────┘    │
│                                                          │
│  [生成文书]                                               │
│                                                          │
│  ── 生成结果 ──────────────────────────────────────────    │
│  ┌──────────────────────────────────────────────────┐    │
│  │  📄 民事起诉状                                     │    │
│  │                                                    │    │
│  │  原告：张三 ...                                     │    │
│  │  被告：李四 ...                                     │    │
│  │  诉讼请求：...                                      │    │
│  │  事实与理由：...                                    │    │
│  │  此致 XXX人民法院                                   │    │
│  │                                                    │    │
│  └──────────────────────────────────────────────────┘    │
│  [复制]  [导出Word]  [发送到对话]                          │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

#### 组件设计

| 组件 | 说明 | 样式 |
|---|---|---|
| `CaseSelector` | 关联案件选择器 | 同案件跟踪页 |
| `TemplateSelector` | 模板下拉选择器 | `glass-input-field` + `FileText` icon |
| `ParamsForm` | 参数填写区 | `glass-card p-4`，每行 `label text-[var(--fs-small)]` + `glass-input-field` |
| `GenerateButton` | 生成按钮 | `PrimaryButton` + `Sparkles` icon，加载时 `glow-pulse` + "AI 思考中..." |
| `DocumentPreview` | 文书预览区 | `glass-card p-6`，正文 `text-[var(--fs-body)] whitespace-pre-wrap font-serif` |
| `ActionBar` | 操作栏 | `SecondaryButton` 组：`Copy` / `Download` / `MessageSquare` icons |

#### 交互流程

1. **选择案件 + 模板**：选择案件 → 自动填充当事人信息到模板；选择模板 → 渲染对应参数表单
2. **生成文书**：填写补充信息 → 点击「生成文书」→ `POST /api/legal/documents/draft {action:"draft"}` → 渲染预览
3. **精调**：在对话区继续精调 → `POST /api/legal/documents/draft {action:"refine"}` → 更新预览
4. **导出/发送**：复制到剪贴板 / 导出 .docx / 发送到对话继续修改

---

## 三、表设计

### 3.1 完整 DDL

```sql
-- ============================================================
-- pc_legal_parties — 当事人表
-- ============================================================
CREATE TABLE IF NOT EXISTS pc_legal_parties (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(200)    NOT NULL,                        -- 姓名/公司名
    id_card         VARCHAR(18),                                     -- 身份证号/统一社会信用代码（自然人18位身份证号/法人18位统一社会信用代码）
    party_type      VARCHAR(16)     NOT NULL DEFAULT 'natural_person', -- 当事人类型：natural_person / legal_entity
    role            VARCHAR(32)     NOT NULL,                        -- 角色：plaintiff / defendant / agent_plaintiff / agent_defendant / third_party / judge
    contact         VARCHAR(500),                                    -- 联系方式（电话/邮箱，JSON 格式存储多种）
    address         VARCHAR(500),                                    -- 住所/注册地址
    organization    VARCHAR(200),                                    -- 所属组织/律所
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

-- 索引：按角色查询当事人
CREATE INDEX IF NOT EXISTS idx_legal_parties_role
    ON pc_legal_parties (role);

-- 索引：按姓名模糊查询
CREATE INDEX IF NOT EXISTS idx_legal_parties_name_trgm
    ON pc_legal_parties USING gin (name gin_trgm_ops);

-- 索引：按身份证号/统一社会信用代码精确查询（利益冲突检查）
CREATE INDEX IF NOT EXISTS idx_legal_parties_id_card
    ON pc_legal_parties (id_card)
    WHERE id_card IS NOT NULL;


-- ============================================================
-- pc_legal_cases — 案件表
-- ============================================================
CREATE TABLE IF NOT EXISTS pc_legal_cases (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    case_number     VARCHAR(100),                                    -- 案号：(2025)京0105民初1234号
    case_type       VARCHAR(64)     NOT NULL,                        -- 案由类型：合同纠纷/侵权责任/劳动争议/知识产权/...
    court           VARCHAR(200),                                    -- 受理法院
    court_level     VARCHAR(16)     DEFAULT 'basic',                 -- 管辖法院层级：basic / intermediate / high / supreme
    status          VARCHAR(32)     NOT NULL DEFAULT 'active',       -- 状态：active / archived / closed
    description     TEXT,                                            -- 案件描述
    limitation_deadline DATE,                                         -- 诉讼时效届满日
    metadata        JSONB           DEFAULT '{}',                    -- 扩展元数据（风险等级、代理律师等）
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

-- 索引：按案号精确查询
CREATE UNIQUE INDEX IF NOT EXISTS idx_legal_cases_case_number
    ON pc_legal_cases (case_number)
    WHERE case_number IS NOT NULL;

-- 索引：按状态筛选
CREATE INDEX IF NOT EXISTS idx_legal_cases_status
    ON pc_legal_cases (status);

-- 索引：按案由类型筛选
CREATE INDEX IF NOT EXISTS idx_legal_cases_type
    ON pc_legal_cases (case_type);

-- 索引：按创建时间倒序
CREATE INDEX IF NOT EXISTS idx_legal_cases_created_desc
    ON pc_legal_cases (created_at DESC);

-- 索引：全文搜索
CREATE INDEX IF NOT EXISTS idx_legal_cases_search
    ON pc_legal_cases USING gin (
        to_tsvector('simple', coalesce(case_number, '') || ' ' || coalesce(description, '') || ' ' || coalesce(case_type, ''))
    );


-- ============================================================
-- pc_legal_documents — 法律文书表
-- ============================================================
CREATE TABLE IF NOT EXISTS pc_legal_documents (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id         UUID            NOT NULL REFERENCES pc_legal_cases(id) ON DELETE CASCADE,
    template        VARCHAR(100),                                    -- 模板名称：民事起诉状/答辩状/催告函/...
    title           VARCHAR(200)    NOT NULL,                        -- 文书标题
    content         TEXT            NOT NULL,                        -- 文书正文（纯文本 / Markdown）
    version         INTEGER         NOT NULL DEFAULT 1,              -- 版本号，refine 时递增
    status          VARCHAR(32)     NOT NULL DEFAULT 'draft',        -- 状态：draft / reviewed / final
    metadata        JSONB           DEFAULT '{}',                    -- 扩展元数据（生成参数、skill_hint 等）
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

-- 索引：按案件查询文书
CREATE INDEX IF NOT EXISTS idx_legal_documents_case_id
    ON pc_legal_documents (case_id);

-- 索引：按模板类型查询
CREATE INDEX IF NOT EXISTS idx_legal_documents_template
    ON pc_legal_documents (template);

-- 索引：按状态筛选
CREATE INDEX IF NOT EXISTS idx_legal_documents_status
    ON pc_legal_documents (status);

-- 索引：按版本倒序（取最新版本）
CREATE INDEX IF NOT EXISTS idx_legal_documents_version_desc
    ON pc_legal_documents (case_id, version DESC);


-- ============================================================
-- pc_legal_timeline_events — 案件时间线事件表
-- ============================================================
CREATE TABLE IF NOT EXISTS pc_legal_timeline_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id         UUID            NOT NULL REFERENCES pc_legal_cases(id) ON DELETE CASCADE,
    event_type      VARCHAR(32)     NOT NULL,                        -- 事件类型：filing / hearing / evidence_exchange / judgment / deadline / other
    event_date      DATE            NOT NULL,                        -- 事件日期
    title           VARCHAR(200)    NOT NULL,                        -- 事件标题
    description     TEXT,                                            -- 事件描述
    is_predicted    BOOLEAN         NOT NULL DEFAULT FALSE,          -- 是否 AI 预测事件
    source          VARCHAR(32)     NOT NULL DEFAULT 'user',         -- 来源：user / agent / system
    metadata        JSONB           DEFAULT '{}',                    -- 扩展元数据
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

-- 索引：按案件 + 日期排序（时间线核心查询）
CREATE INDEX IF NOT EXISTS idx_legal_timeline_case_date
    ON pc_legal_timeline_events (case_id, event_date ASC);

-- 索引：按事件类型筛选
CREATE INDEX IF NOT EXISTS idx_legal_timeline_event_type
    ON pc_legal_timeline_events (event_type);

-- 索引：查询即将到期事件（提醒功能）
CREATE INDEX IF NOT EXISTS idx_legal_timeline_upcoming
    ON pc_legal_timeline_events (event_date ASC)
    WHERE is_predicted = FALSE AND event_date >= CURRENT_DATE;

-- 索引：查询 AI 预测事件
CREATE INDEX IF NOT EXISTS idx_legal_timeline_predicted
    ON pc_legal_timeline_events (case_id)
    WHERE is_predicted = TRUE;


-- ============================================================
-- pc_legal_deadlines — 法定期限跟踪表
-- ============================================================
CREATE TABLE IF NOT EXISTS pc_legal_deadlines (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id         UUID            NOT NULL REFERENCES pc_legal_cases(id) ON DELETE CASCADE,
    deadline_type   VARCHAR(64)     NOT NULL,                        -- 期限类型：defense / appeal_judgment / appeal_ruling / evidence_ordinary / evidence_summary / execution / preservation_30d / other
    trigger_date    DATE            NOT NULL,                        -- 触发日期（如收到起诉状之日、判决书送达之日）
    deadline_date   DATE            NOT NULL,                        -- 期限届满日（已含节假日顺延）
    is_completed    BOOLEAN         NOT NULL DEFAULT FALSE,          -- 是否已完成
    completed_at    TIMESTAMPTZ,                                      -- 完成时间
    metadata        JSONB           DEFAULT '{}',                    -- 扩展元数据（原始天数、中止/中断记录等）
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

-- 索引：按案件查询期限
CREATE INDEX IF NOT EXISTS idx_legal_deadlines_case
    ON pc_legal_deadlines (case_id);

-- 索引：查询未完成期限（提醒功能）
CREATE INDEX IF NOT EXISTS idx_legal_deadlines_pending
    ON pc_legal_deadlines (deadline_date ASC)
    WHERE is_completed = FALSE;

-- 索引：按期限类型筛选
CREATE INDEX IF NOT EXISTS idx_legal_deadlines_type
    ON pc_legal_deadlines (deadline_type);
```

### 3.2 表关系

```
pc_legal_cases (1) ──< (N) pc_legal_parties         通过关联表（见下方）
pc_legal_cases (1) ──< (N) pc_legal_documents       case_id FK
pc_legal_cases (1) ──< (N) pc_legal_timeline_events  case_id FK
pc_legal_cases (1) ──< (N) pc_legal_deadlines        case_id FK
```

案件与当事人为多对多关系（同一当事人可出现在多个案件中），需关联表：

```sql
-- ============================================================
-- pc_legal_case_parties — 案件-当事人关联表
-- ============================================================
CREATE TABLE IF NOT EXISTS pc_legal_case_parties (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id         UUID            NOT NULL REFERENCES pc_legal_cases(id) ON DELETE CASCADE,
    party_id        UUID            NOT NULL REFERENCES pc_legal_parties(id) ON DELETE CASCADE,
    role_in_case    VARCHAR(32)     NOT NULL,                        -- 本案角色（与 parties.role 冗余，允许同当事人在不同案件中角色不同）
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_case_party UNIQUE (case_id, party_id)
);

CREATE INDEX IF NOT EXISTS idx_legal_case_parties_case
    ON pc_legal_case_parties (case_id);

CREATE INDEX IF NOT EXISTS idx_legal_case_parties_party
    ON pc_legal_case_parties (party_id);
```

---

## 四、LegalBundle 实现设计

### 4.1 完整 Bundle 类

```python
# backend/src/subpackages.legal/bundle.py
from prismclaw.schema.services.domain import (
    DomainBundle,
    DomainInfrastructureContext,
    DomainRuntimeContext,
    DomainLifecycleContext,
    DomainManifest,
    MenuEntry,
)


class LegalBundle:
    """法律域 Bundle — 包装 legal-chineselaw 插件能力 + 提供页面入口。

    核心原则：
    - on_build_runtime 为空：legal-chineselaw 插件已注册全部工具/技能/MCP
    - 本 Bundle 只做两件事：(1) 提供数据库 Schema (2) 提供 REST 路由 + 前端菜单
    """

    name = "legal"
    required_core_version = ">=0.1.0"
    depends_on: list[str] = []

    # ── 基础设施：数据库 Schema ──────────────────────────────

    def on_build_infrastructure(self, ctx: DomainInfrastructureContext) -> None:
        from .persistence.case_store import CaseStore
        from .persistence.document_store import DocumentStore
        from .persistence.party_store import PartyStore
        from .persistence.timeline_store import TimelineStore
        from .persistence.ensure_schema import ensure_legal_schema

        ctx.register_store("case", CaseStore(ctx.db_engine))
        ctx.register_store("document", DocumentStore(ctx.db_engine))
        ctx.register_store("party", PartyStore(ctx.db_engine))
        ctx.register_store("timeline", TimelineStore(ctx.db_engine))

        import asyncio
        asyncio.get_event_loop().run_until_complete(
            ensure_legal_schema(ctx.db_engine)
        )

    # ── 运行时：空 — legal-chineselaw 已注册全部能力 ──────────

    def on_build_runtime(self, ctx: DomainRuntimeContext) -> None:
        """空实现 — legal-chineselaw 插件已注册全部工具/技能/MCP（12 子域、20+ 命令、7+ 工作流）。
        DomainBundle 只需提供 REST 路由入口，按总纲模式路由到 create_domain_session() 即可。
        """
        pass

    # ── 生命周期：REST 路由注册 ───────────────────────────────

    def on_build_lifecycle(self, ctx: DomainLifecycleContext) -> None:
        from .routes.agent import router as agent_router
        from .routes.case import router as case_router
        from .routes.track import router as track_router
        from .routes.draft import router as draft_router
        from .routes.party import router as party_router

        ctx.register_route(agent_router)
        ctx.register_route(case_router)
        ctx.register_route(track_router)
        ctx.register_route(draft_router)
        ctx.register_route(party_router)

    # ── 前端菜单/页面描述 ─────────────────────────────────────

    def get_manifest(self) -> DomainManifest:
        return DomainManifest(
            key="legal",
            label="法律",
            icon="scale",
            path="/legal",                       # 工作台路由，不再平铺子路由
            menus=[
                MenuEntry(key="cases", label="案件管理", icon="folder-open"),
                MenuEntry(key="track", label="案件跟踪", icon="activity"),
                MenuEntry(key="draft", label="文书起草", icon="file-text"),
            ],
        )
```

### 4.2 REST 路由实现

#### routes/agent.py — 域 Agent 会话管理

```python
from fastapi import APIRouter
from prismclaw.schema.agent.run_request import RunRequest

router = APIRouter(prefix="/api/legal/agent", tags=["legal"])


@router.post("/run")
async def run_legal_agent(request: RunRequest):
    """创建法律域 Agent 会话并执行。按总纲 create_domain_session 模式调用。
    法律域特有：GDPR 脱敏钩子、AI 治理合规规则自动生效（如已启用）。
    """
    from prismclaw.runtime.facade.chat import create_domain_session
    result = await create_domain_session(
        domain="legal",
        message=request.message,
        files=request.files,
        skill_hint=request.skill_hint,
    )
    return result
```

#### routes/case.py — 案件管理页面 API

```python
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional

router = APIRouter(prefix="/api/legal/cases", tags=["legal"])


class CaseManageRequest(BaseModel):
    action: str                          # create / query / update / archive
    case_id: Optional[str] = None
    case_data: Optional[dict] = None
    filters: Optional[dict] = None
    skill_hint: str = "案件管理"


class CaseManageResponse(BaseModel):
    ok: bool
    action: str
    case: Optional[dict] = None
    cases: Optional[list[dict]] = None
    agent_result: Optional[dict] = None


@router.post("/manage", response_model=CaseManageResponse)
async def manage_case(request: CaseManageRequest):
    """案件管理 — 创建/查询/更新/归档案件。"""
    from prismclaw.runtime.facade.chat import create_domain_session
    from prismclaw.app import get_app_state

    state = get_app_state()
    case_store = state.domain_stores["case"]

    if request.action == "create":
        # 1. 写入数据库
        case = await case_store.create(request.case_data)
        # 2. Agent 分析案件
        agent_result = await create_domain_session(
            domain="legal",
            message=f"请分析新建案件：{request.case_data}",
            skill_hint=request.skill_hint,
        )
        return CaseManageResponse(ok=True, action="create", case=case, agent_result=agent_result)

    elif request.action == "query":
        cases = await case_store.query(request.filters or {})
        return CaseManageResponse(ok=True, action="query", cases=cases)

    elif request.action == "update":
        case = await case_store.update(request.case_id, request.case_data)
        return CaseManageResponse(ok=True, action="update", case=case)

    elif request.action == "archive":
        case = await case_store.archive(request.case_id)
        return CaseManageResponse(ok=True, action="archive", case=case)

    else:
        return CaseManageResponse(ok=False, action=request.action)
```

#### routes/track.py — 案件跟踪页面 API

```python
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional

router = APIRouter(prefix="/api/legal/cases", tags=["legal"])


class TrackRequest(BaseModel):
    action: str                          # analyze / add_event / timeline
    case_id: str
    event: Optional[dict] = None
    skill_hint: str = "案件跟踪"


class TrackResponse(BaseModel):
    ok: bool
    action: str
    timeline: Optional[list[dict]] = None
    ai_analysis: Optional[dict] = None


@router.post("/track", response_model=TrackResponse)
async def track_case(request: TrackRequest):
    """案件跟踪 — 时间线/添加事件/AI 分析。"""
    from prismclaw.runtime.facade.chat import create_domain_session
    from prismclaw.app import get_app_state

    state = get_app_state()
    timeline_store = state.domain_stores["timeline"]

    if request.action == "add_event":
        event = await timeline_store.add_event(request.case_id, request.event)
        return TrackResponse(ok=True, action="add_event", timeline=[event])

    elif request.action == "timeline":
        timeline = await timeline_store.get_timeline(request.case_id)
        return TrackResponse(ok=True, action="timeline", timeline=timeline)

    elif request.action == "analyze":
        timeline = await timeline_store.get_timeline(request.case_id)
        agent_result = await create_domain_session(
            domain="legal",
            message=f"请分析案件 {request.case_id} 的进展，给出下一步建议和风险提醒",
            skill_hint=request.skill_hint,
        )
        return TrackResponse(
            ok=True,
            action="analyze",
            timeline=timeline,
            ai_analysis=agent_result,
        )

    return TrackResponse(ok=False, action=request.action)
```

#### routes/draft.py — 文书起草页面 API

```python
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional

router = APIRouter(prefix="/api/legal/documents", tags=["legal"])


class DraftRequest(BaseModel):
    action: str                          # draft / refine / list_templates
    case_id: Optional[str] = None
    template: Optional[str] = None
    params: Optional[dict] = None
    draft_id: Optional[str] = None
    refine_instruction: Optional[str] = None
    skill_hint: str = "文书起草"


class DraftResponse(BaseModel):
    ok: bool
    action: str
    document: Optional[dict] = None
    templates: Optional[list[str]] = None


@router.post("/draft", response_model=DraftResponse)
async def draft_document(request: DraftRequest):
    """文书起草 — 生成/精调/模板列表。"""
    from prismclaw.runtime.facade.chat import create_domain_session
    from prismclaw.app import get_app_state

    state = get_app_state()
    document_store = state.domain_stores["document"]

    if request.action == "list_templates":
        templates = await document_store.list_templates()
        return DraftResponse(ok=True, action="list_templates", templates=templates)

    elif request.action == "draft":
        message = f"请根据模板「{request.template}」起草法律文书，参数：{request.params}"
        if request.case_id:
            message += f"，关联案件：{request.case_id}"
        agent_result = await create_domain_session(
            domain="legal",
            message=message,
            skill_hint=request.skill_hint,
        )
        doc = await document_store.create(
            case_id=request.case_id,
            template=request.template,
            content=agent_result.get("output", ""),
            params=request.params,
        )
        return DraftResponse(ok=True, action="draft", document=doc)

    elif request.action == "refine":
        message = f"请精调文书：{request.refine_instruction}"
        agent_result = await create_domain_session(
            domain="legal",
            message=message,
            skill_hint=request.skill_hint,
        )
        doc = await document_store.refine(
            draft_id=request.draft_id,
            content=agent_result.get("output", ""),
        )
        return DraftResponse(ok=True, action="refine", document=doc)

    return DraftResponse(ok=False, action=request.action)
```

#### routes/party.py — 当事人管理 API

```python
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional

router = APIRouter(prefix="/api/legal/parties", tags=["legal"])


class PartyRequest(BaseModel):
    action: str                          # create / query / link_to_case
    party_data: Optional[dict] = None
    party_id: Optional[str] = None
    case_id: Optional[str] = None
    role_in_case: Optional[str] = None
    filters: Optional[dict] = None


class PartyResponse(BaseModel):
    ok: bool
    action: str
    party: Optional[dict] = None
    parties: Optional[list[dict]] = None


@router.post("/manage", response_model=PartyResponse)
async def manage_party(request: PartyRequest):
    """当事人管理 — 创建/查询/关联到案件。"""
    from prismclaw.app import get_app_state

    state = get_app_state()
    party_store = state.domain_stores["party"]

    if request.action == "create":
        party = await party_store.create(request.party_data)
        return PartyResponse(ok=True, action="create", party=party)

    elif request.action == "query":
        parties = await party_store.query(request.filters or {})
        return PartyResponse(ok=True, action="query", parties=parties)

    elif request.action == "link_to_case":
        await party_store.link_to_case(
            party_id=request.party_id,
            case_id=request.case_id,
            role_in_case=request.role_in_case,
        )
        return PartyResponse(ok=True, action="link_to_case")

    return PartyResponse(ok=False, action=request.action)
```

### 4.3 入口点注册配置

```toml
# pyproject.toml
[project]
name = "subpackages.legal"
version = "0.1.0"
description = "PrismClaw 法律域模块 — 案件管理、案件跟踪、文书起草"
requires-python = ">=3.11"
dependencies = [
    "prismclaw>=0.1.0",
]

[project.entry-points."prismclaw.domains"]
legal = "subpackages.legal.bundle:LegalBundle"
```

```yaml
# configs/settings.yaml
domains:
  enabled:
    - legal
```

---

## 五、前端组件设计

### 5.1 CaseManagePage.tsx — 案件管理页面

```tsx
import { useState, useEffect, useCallback } from 'react'
import { Plus, Search, FolderOpen, Scale, ArrowRight, FileText } from 'lucide-react'
import { PrimaryButton } from '@/components/ui/PrimaryButton'
import { SecondaryButton } from '@/components/ui/SecondaryButton'

interface Case {
  id: string
  case_number: string
  case_type: string
  court: string
  status: 'active' | 'archived' | 'closed'
  description: string
  parties: { id: string; name: string; role: string }[]
  created_at: string
  updated_at: string
}

export function CaseManagePage() {
  const [cases, setCases] = useState<Case[]>([])
  const [loading, setLoading] = useState(false)
  const [filter, setFilter] = useState<'all' | 'active' | 'archived'>('all')
  const [searchQuery, setSearchQuery] = useState('')
  const [showNewCaseModal, setShowNewCaseModal] = useState(false)

  const fetchCases = useCallback(async () => {
    setLoading(true)
    try {
      const res = await fetch('/api/legal/cases/manage', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          action: 'query',
          filters: { status: filter, keyword: searchQuery },
        }),
      })
      const data = await res.json()
      if (data.ok) setCases(data.cases ?? [])
    } finally {
      setLoading(false)
    }
  }, [filter, searchQuery])

  useEffect(() => { fetchCases() }, [fetchCases])

  const handleNewCase = async (caseData: Partial<Case>) => {
    await fetch('/api/legal/cases/manage', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'create', case_data: caseData, skill_hint: '案件管理' }),
    })
    setShowNewCaseModal(false)
    fetchCases()
  }

  const navigateToTrack = (caseId: string) => {
    window.location.hash = `/legal/track?case_id=${caseId}`
  }

  const navigateToDraft = (caseId: string) => {
    window.location.hash = `/legal/draft?case_id=${caseId}`
  }

  const navigateToChat = (caseItem: Case) => {
    setActiveSection('chat')
    setPendingComposerPrefill(`请分析案件 ${caseItem.case_number} 的详情`)
  }

  return (
    <div className="flex-1 overflow-hidden flex flex-col min-w-[950px]">
      {/* 顶部栏 */}
      <div className="px-8 pt-7 pb-5 shrink-0">
        <h1 className="text-[length:var(--fs-title)] font-semibold text-[var(--c-text)]">案件管理</h1>
        <p className="text-[length:var(--fs-body)] text-[var(--c-text-secondary)] mt-1">
          AI 辅助创建和管理案件
        </p>
      </div>

      {/* 操作栏 */}
      <div className="px-8 pb-4 flex items-center gap-3 shrink-0">
        <PrimaryButton className="h-9 px-4 rounded-lg font-medium inline-flex items-center gap-2"
                        onClick={() => setShowNewCaseModal(true)}>
          <Plus size={16} /> 新建案件
        </PrimaryButton>

        <div className="relative flex-1 max-w-xs">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--c-text-secondary)]" />
          <input
            className="glass-input-field w-full h-9 pl-9 pr-3 rounded-lg text-[length:var(--fs-ui)]"
            placeholder="搜索案件..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
        </div>

        <div className="flex items-center gap-1 ml-auto">
          {(['all', 'active', 'archived'] as const).map((f) => (
            <SecondaryButton
              key={f}
              className={`h-8 px-3 rounded-lg text-[length:var(--fs-small)] ${
                filter === f ? 'bg-[var(--c-surface-2)] border-[var(--c-accent)]' : ''
              }`}
              onClick={() => setFilter(f)}
            >
              {f === 'all' ? '全部' : f === 'active' ? '进行中' : '已归档'}
            </SecondaryButton>
          ))}
        </div>
      </div>

      {/* 案件列表 */}
      <div className="flex-1 overflow-y-auto px-8 py-6">
        {loading ? (
          <div className="flex items-center justify-center py-12">
            <div className="glow-pulse" />
            <span className="text-[length:var(--fs-body)] text-[var(--c-text-secondary)] ml-3">加载中...</span>
          </div>
        ) : cases.length === 0 ? (
          <div className="text-center py-12 text-[length:var(--fs-body)] text-[var(--c-text-secondary)]">
            暂无案件，点击「新建案件」开始
          </div>
        ) : (
          <div className="space-y-4 max-w-[1200px] mx-auto">
            {cases.map((c) => (
              <div key={c.id} className="glass-card p-5">
                <div className="flex items-start justify-between">
                  <div>
                    <div className="flex items-center gap-2">
                      <Scale size={16} className="text-[var(--c-accent)]" />
                      <span className="text-[length:var(--fs-heading)] font-semibold text-[var(--c-text)]">
                        {c.parties.map(p => p.name).join(' vs ')} · {c.case_type}
                      </span>
                    </div>
                    <div className="text-[length:var(--fs-caption)] text-[var(--c-text-secondary)] mt-1">
                      案号：{c.case_number}
                    </div>
                    <div className="flex items-center gap-3 mt-2">
                      {c.parties.map((p) => (
                        <span key={p.id} className={`
                          inline-flex px-2 py-0.5 rounded text-[length:var(--fs-caption)]
                          ${p.role === 'plaintiff' ? 'bg-blue-500/10 text-blue-400' :
                            p.role === 'defendant' ? 'bg-red-500/10 text-red-400' :
                            'bg-gray-500/10 text-gray-400'}
                        `}>
                          {p.role === 'plaintiff' ? '原告' :
                           p.role === 'defendant' ? '被告' :
                           p.role.startsWith('agent') ? '代理人' : '第三方'}：{p.name}
                        </span>
                      ))}
                    </div>
                    <div className="text-[length:var(--fs-caption)] text-[var(--c-text-secondary)] mt-2">
                      状态：{c.status === 'active' ? '进行中' : c.status === 'archived' ? '已归档' : '已关闭'}
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-2 mt-4 pt-3 border-t border-[var(--c-border)]">
                  <SecondaryButton className="h-8 px-3 rounded-lg text-[length:var(--fs-small)]"
                                   onClick={() => navigateToChat(c)}>
                    详情
                  </SecondaryButton>
                  <SecondaryButton className="h-8 px-3 rounded-lg text-[length:var(--fs-small)] inline-flex items-center gap-1"
                                   onClick={() => navigateToTrack(c.id)}>
                    <ArrowRight size={14} /> 跟踪
                  </SecondaryButton>
                  <SecondaryButton className="h-8 px-3 rounded-lg text-[length:var(--fs-small)] inline-flex items-center gap-1"
                                   onClick={() => navigateToDraft(c.id)}>
                    <FileText size={14} /> 起草文书
                  </SecondaryButton>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* 新建案件模态框 — 简化示意 */}
      {showNewCaseModal && (
        <NewCaseModal
          onClose={() => setShowNewCaseModal(false)}
          onSubmit={handleNewCase}
        />
      )}
    </div>
  )
}

// 辅助函数（实际从 context 获取）
declare function setActiveSection(s: string): void
declare function setPendingComposerPrefill(s: string): void

// 新建案件模态框（简化）
function NewCaseModal({ onClose, onSubmit }: {
  onClose: () => void
  onSubmit: (data: Partial<Case>) => void
}) {
  const [form, setForm] = useState({
    case_number: '', case_type: '', court: '', description: '',
  })

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="glass-card p-6 w-[560px] max-h-[80vh] overflow-y-auto">
        <h2 className="text-[length:var(--fs-heading)] font-semibold text-[var(--c-text)]">新建案件</h2>
        <div className="space-y-4 mt-4">
          <input className="glass-input-field w-full h-9 px-3 rounded-lg text-[length:var(--fs-ui)]"
                 placeholder="案号" value={form.case_number}
                 onChange={(e) => setForm(f => ({ ...f, case_number: e.target.value }))} />
          <input className="glass-input-field w-full h-9 px-3 rounded-lg text-[length:var(--fs-ui)]"
                 placeholder="案由（合同纠纷/侵权责任/...）" value={form.case_type}
                 onChange={(e) => setForm(f => ({ ...f, case_type: e.target.value }))} />
          <input className="glass-input-field w-full h-9 px-3 rounded-lg text-[length:var(--fs-ui)]"
                 placeholder="受理法院" value={form.court}
                 onChange={(e) => setForm(f => ({ ...f, court: e.target.value }))} />
          <textarea className="glass-input-field w-full h-24 px-3 py-2 rounded-lg text-[length:var(--fs-ui)]"
                    placeholder="案件描述" value={form.description}
                    onChange={(e) => setForm(f => ({ ...f, description: e.target.value }))} />
        </div>
        <div className="flex items-center justify-end gap-2 mt-6">
          <SecondaryButton className="h-9 px-4 rounded-lg text-[length:var(--fs-ui)]" onClick={onClose}>
            取消
          </SecondaryButton>
          <PrimaryButton className="h-9 px-4 rounded-lg text-[length:var(--fs-ui)] font-medium"
                         onClick={() => onSubmit(form)}>
            创建
          </PrimaryButton>
        </div>
      </div>
    </div>
  )
}
```

### 5.2 CaseTrackPage.tsx — 案件跟踪页面

```tsx
import { useState, useEffect, useCallback } from 'react'
import { Activity, Plus, MessageSquare, AlertTriangle, Circle, CircleDot, Diamond } from 'lucide-react'
import { PrimaryButton } from '@/components/ui/PrimaryButton'
import { SecondaryButton } from '@/components/ui/SecondaryButton'

interface TimelineEvent {
  id: string
  case_id: string
  event_type: string
  event_date: string
  title: string
  description: string
  is_predicted: boolean
}

interface CaseOption {
  id: string
  case_number: string
  case_type: string
  parties: { name: string }[]
}

export function CaseTrackPage() {
  const [cases, setCases] = useState<CaseOption[]>([])
  const [selectedCaseId, setSelectedCaseId] = useState<string>('')
  const [timeline, setTimeline] = useState<TimelineEvent[]>([])
  const [aiAnalysis, setAiAnalysis] = useState<any>(null)
  const [loading, setLoading] = useState(false)
  const [showAddEventModal, setShowAddEventModal] = useState(false)

  // 加载案件列表
  useEffect(() => {
    fetch('/api/legal/cases/manage', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'query', filters: { status: 'active' } }),
    })
      .then(r => r.json())
      .then(data => {
        if (data.ok) {
          setCases(data.cases ?? [])
          if (data.cases?.length && !selectedCaseId) {
            setSelectedCaseId(data.cases[0].id)
          }
        }
      })
  }, [])

  // 加载时间线
  const fetchTimeline = useCallback(async () => {
    if (!selectedCaseId) return
    setLoading(true)
    try {
      const res = await fetch('/api/legal/cases/track', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'timeline', case_id: selectedCaseId }),
      })
      const data = await res.json()
      if (data.ok) setTimeline(data.timeline ?? [])
    } finally {
      setLoading(false)
    }
  }, [selectedCaseId])

  useEffect(() => { fetchTimeline() }, [fetchTimeline])

  // AI 分析
  const analyzeCase = async () => {
    if (!selectedCaseId) return
    setLoading(true)
    try {
      const res = await fetch('/api/legal/cases/track', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'analyze', case_id: selectedCaseId, skill_hint: '案件跟踪' }),
      })
      const data = await res.json()
      if (data.ok) {
        setTimeline(data.timeline ?? [])
        setAiAnalysis(data.ai_analysis)
      }
    } finally {
      setLoading(false)
    }
  }

  // 添加事件
  const handleAddEvent = async (event: Partial<TimelineEvent>) => {
    await fetch('/api/legal/cases/track', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'add_event', case_id: selectedCaseId, event }),
    })
    setShowAddEventModal(false)
    fetchTimeline()
  }

  const eventTypeLabel: Record<string, string> = {
    filing: '立案', hearing: '开庭', evidence_exchange: '证据交换',
    judgment: '判决', deadline: '截止日', other: '其他',
  }

  return (
    <div className="flex-1 overflow-hidden flex flex-col min-w-[950px]">
      {/* 顶部栏 */}
      <div className="px-8 pt-7 pb-5 shrink-0">
        <h1 className="text-[length:var(--fs-title)] font-semibold text-[var(--c-text)]">案件跟踪</h1>
        <p className="text-[length:var(--fs-body)] text-[var(--c-text-secondary)] mt-1">
          AI 分析案件进展与下一步建议
        </p>
      </div>

      {/* 案件选择器 */}
      <div className="px-8 pb-4 shrink-0">
        <select
          className="glass-input-field h-9 px-3 rounded-lg text-[length:var(--fs-ui)] min-w-[280px]"
          value={selectedCaseId}
          onChange={(e) => setSelectedCaseId(e.target.value)}
        >
          {cases.map((c) => (
            <option key={c.id} value={c.id}>
              {c.parties.map(p => p.name).join(' vs ')} · {c.case_type}
            </option>
          ))}
        </select>
      </div>

      {/* 内容区 */}
      <div className="flex-1 overflow-y-auto px-8 py-6">
        <div className="max-w-[1200px] mx-auto space-y-6">

          {/* 时间线 */}
          <div className="glass-card p-6">
            <h2 className="text-[length:var(--fs-heading)] font-semibold text-[var(--c-text)] mb-4 flex items-center gap-2">
              <Activity size={16} className="text-[var(--c-accent)]" /> 时间线
            </h2>
            {loading ? (
              <div className="flex items-center py-8">
                <div className="glow-pulse" />
                <span className="text-[length:var(--fs-body)] text-[var(--c-text-secondary)] ml-3">AI 思考中...</span>
              </div>
            ) : (
              <div className="flex flex-col">
                {timeline.map((evt, idx) => (
                  <div key={evt.id} className="flex items-start gap-3">
                    {/* 节点图标 */}
                    <div className="flex flex-col items-center">
                      {evt.is_predicted ? (
                        <Diamond size={14} className="text-[var(--c-text-secondary)] mt-1" />
                      ) : idx < timeline.filter(e => !e.is_predicted).length - 1 ? (
                        <CircleDot size={14} className="text-[var(--c-accent)]" />
                      ) : (
                        <Circle size={14} className="text-[var(--c-accent)] border-2" />
                      )}
                      {idx < timeline.length - 1 && (
                        <div className="w-px h-8 bg-[var(--c-border)]" />
                      )}
                    </div>
                    {/* 事件内容 */}
                    <div className="pb-4">
                      <div className="flex items-center gap-2">
                        <span className="text-[length:var(--fs-body)] font-medium text-[var(--c-text)]">
                          {evt.event_date}
                        </span>
                        <span className="text-[length:var(--fs-caption)] text-[var(--c-text-secondary)]">
                          {eventTypeLabel[evt.event_type] || evt.event_type}
                          {evt.is_predicted && '（AI 预测）'}
                        </span>
                      </div>
                      <div className="text-[length:var(--fs-body)] text-[var(--c-text)] mt-0.5">
                        {evt.title}
                      </div>
                      {evt.description && (
                        <div className="text-[length:var(--fs-caption)] text-[var(--c-text-secondary)] mt-0.5">
                          {evt.description}
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* AI 建议 */}
          {aiAnalysis && (
            <div className="glass-card p-6">
              <h2 className="text-[length:var(--fs-heading)] font-semibold text-[var(--c-text)] mb-3">
                AI 建议
              </h2>
              {aiAnalysis.next_steps && (
                <div className="space-y-1 mb-3">
                  {aiAnalysis.next_steps.map((step: string, i: number) => (
                    <div key={i} className="text-[length:var(--fs-body)] text-[var(--c-text)]">
                      {i + 1}. {step}
                    </div>
                  ))}
                </div>
              )}
              {aiAnalysis.risk_flags?.length > 0 && (
                <div className="space-y-1">
                  {aiAnalysis.risk_flags.map((flag: string, i: number) => (
                    <div key={i} className="flex items-center gap-1 text-[length:var(--fs-caption)] text-[var(--c-error)]">
                      <AlertTriangle size={12} /> {flag}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* 底部操作栏 */}
      <div className="px-8 py-4 shrink-0 flex items-center gap-3 border-t border-[var(--c-border)]">
        <PrimaryButton className="h-9 px-4 rounded-lg font-medium inline-flex items-center gap-2"
                       onClick={() => setShowAddEventModal(true)}>
          <Plus size={16} /> 添加事件
        </PrimaryButton>
        <SecondaryButton className="h-9 px-4 rounded-lg inline-flex items-center gap-2"
                         onClick={analyzeCase}>
          AI 分析
        </SecondaryButton>
        <SecondaryButton className="h-9 px-4 rounded-lg inline-flex items-center gap-2 ml-auto"
                         onClick={() => {
                           setActiveSection('chat')
                           setPendingComposerPrefill(`请分析案件进展`)
                         }}>
          <MessageSquare size={16} /> 发送到对话
        </SecondaryButton>
      </div>

      {/* 添加事件模态框 */}
      {showAddEventModal && (
        <AddEventModal
          onClose={() => setShowAddEventModal(false)}
          onSubmit={handleAddEvent}
        />
      )}
    </div>
  )
}

declare function setActiveSection(s: string): void
declare function setPendingComposerPrefill(s: string): void

function AddEventModal({ onClose, onSubmit }: {
  onClose: () => void
  onSubmit: (event: Partial<TimelineEvent>) => void
}) {
  const [form, setForm] = useState({
    event_type: 'hearing', event_date: '', title: '', description: '',
  })

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="glass-card p-6 w-[480px]">
        <h2 className="text-[length:var(--fs-heading)] font-semibold text-[var(--c-text)]">添加事件</h2>
        <div className="space-y-4 mt-4">
          <select className="glass-input-field w-full h-9 px-3 rounded-lg text-[length:var(--fs-ui)]"
                  value={form.event_type}
                  onChange={(e) => setForm(f => ({ ...f, event_type: e.target.value }))}>
            <option value="filing">立案</option>
            <option value="hearing">开庭</option>
            <option value="evidence_exchange">证据交换</option>
            <option value="judgment">判决</option>
            <option value="deadline">截止日</option>
            <option value="other">其他</option>
          </select>
          <input type="date" className="glass-input-field w-full h-9 px-3 rounded-lg text-[length:var(--fs-ui)]"
                 value={form.event_date}
                 onChange={(e) => setForm(f => ({ ...f, event_date: e.target.value }))} />
          <input className="glass-input-field w-full h-9 px-3 rounded-lg text-[length:var(--fs-ui)]"
                 placeholder="事件标题" value={form.title}
                 onChange={(e) => setForm(f => ({ ...f, title: e.target.value }))} />
          <textarea className="glass-input-field w-full h-20 px-3 py-2 rounded-lg text-[length:var(--fs-ui)]"
                    placeholder="事件描述（可选）" value={form.description}
                    onChange={(e) => setForm(f => ({ ...f, description: e.target.value }))} />
        </div>
        <div className="flex items-center justify-end gap-2 mt-6">
          <SecondaryButton className="h-9 px-4 rounded-lg" onClick={onClose}>取消</SecondaryButton>
          <PrimaryButton className="h-9 px-4 rounded-lg font-medium"
                         onClick={() => onSubmit({ ...form, is_predicted: false })}>
            添加
          </PrimaryButton>
        </div>
      </div>
    </div>
  )
}
```

### 5.3 DocumentDraftPage.tsx — 文书起草页面

```tsx
import { useState, useEffect } from 'react'
import { FileText, Sparkles, Copy, Download, MessageSquare, ChevronDown } from 'lucide-react'
import { PrimaryButton } from '@/components/ui/PrimaryButton'
import { SecondaryButton } from '@/components/ui/SecondaryButton'

interface CaseOption {
  id: string
  case_number: string
  case_type: string
  parties: { name: string; role: string }[]
}

interface Document {
  id: string
  case_id: string
  template: string
  title: string
  content: string
  version: number
  status: string
}

const TEMPLATES = [
  '民事起诉状', '民事答辩状', '催告函', '停止侵权函',
  'DMCA下架通知', '劳动政策', 'DSAR回复', '隐私影响评估',
  '诉讼简报', '法律保全令', '调查备忘录', '董事会决议',
]

export function DocumentDraftPage() {
  const [cases, setCases] = useState<CaseOption[]>([])
  const [selectedCaseId, setSelectedCaseId] = useState<string>('')
  const [selectedTemplate, setSelectedTemplate] = useState<string>('')
  const [params, setParams] = useState({ claims: '', facts_and_reasons: '', evidence_list: '' })
  const [document, setDocument] = useState<Document | null>(null)
  const [loading, setLoading] = useState(false)

  // 从 URL 参数获取 case_id
  useEffect(() => {
    const urlParams = new URLSearchParams(window.location.hash.split('?')[1] || '')
    const caseId = urlParams.get('case_id')
    if (caseId) setSelectedCaseId(caseId)
  }, [])

  // 加载案件列表
  useEffect(() => {
    fetch('/api/legal/cases/manage', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'query', filters: { status: 'active' } }),
    })
      .then(r => r.json())
      .then(data => {
        if (data.ok) setCases(data.cases ?? [])
      })
  }, [])

  // 生成文书
  const handleDraft = async () => {
    setLoading(true)
    try {
      const res = await fetch('/api/legal/documents/draft', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          action: 'draft',
          case_id: selectedCaseId || undefined,
          template: selectedTemplate,
          params,
          skill_hint: '文书起草',
        }),
      })
      const data = await res.json()
      if (data.ok) setDocument(data.document)
    } finally {
      setLoading(false)
    }
  }

  // 精调文书
  const handleRefine = async (instruction: string) => {
    if (!document) return
    setLoading(true)
    try {
      const res = await fetch('/api/legal/documents/draft', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          action: 'refine',
          draft_id: document.id,
          refine_instruction: instruction,
          skill_hint: '文书起草',
        }),
      })
      const data = await res.json()
      if (data.ok) setDocument(data.document)
    } finally {
      setLoading(false)
    }
  }

  // 复制到剪贴板
  const handleCopy = () => {
    if (document?.content) {
      navigator.clipboard.writeText(document.content)
    }
  }

  // 发送到对话
  const handleSendToChat = () => {
    setActiveSection('chat')
    setPendingComposerPrefill(`请帮我精调以下法律文书：\n${document?.content?.slice(0, 200)}...`)
  }

  return (
    <div className="flex-1 overflow-hidden flex flex-col min-w-[950px]">
      {/* 顶部栏 */}
      <div className="px-8 pt-7 pb-5 shrink-0">
        <h1 className="text-[length:var(--fs-title)] font-semibold text-[var(--c-text)]">文书起草</h1>
        <p className="text-[length:var(--fs-body)] text-[var(--c-text-secondary)] mt-1">
          AI 根据模板和案件信息生成法律文书
        </p>
      </div>

      {/* 选择器 + 参数区 */}
      <div className="px-8 pb-4 shrink-0">
        <div className="max-w-[1200px] mx-auto space-y-4">
          {/* 案件 + 模板选择 */}
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2 flex-1">
              <label className="text-[length:var(--fs-small)] text-[var(--c-text-secondary)] whitespace-nowrap">
                关联案件：
              </label>
              <select
                className="glass-input-field h-9 px-3 rounded-lg text-[length:var(--fs-ui)] flex-1"
                value={selectedCaseId}
                onChange={(e) => setSelectedCaseId(e.target.value)}
              >
                <option value="">不关联案件</option>
                {cases.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.parties.map(p => p.name).join(' vs ')} · {c.case_type}
                  </option>
                ))}
              </select>
            </div>
            <div className="flex items-center gap-2 flex-1">
              <label className="text-[length:var(--fs-small)] text-[var(--c-text-secondary)] whitespace-nowrap">
                文书模板：
              </label>
              <select
                className="glass-input-field h-9 px-3 rounded-lg text-[length:var(--fs-ui)] flex-1"
                value={selectedTemplate}
                onChange={(e) => setSelectedTemplate(e.target.value)}
              >
                <option value="">选择模板</option>
                {TEMPLATES.map((t) => (
                  <option key={t} value={t}>{t}</option>
                ))}
              </select>
            </div>
          </div>

          {/* 补充信息 */}
          <div className="glass-card p-4">
            <h3 className="text-[length:var(--fs-small)] text-[var(--c-text-secondary)] mb-3">
              补充信息（可选）
            </h3>
            <div className="space-y-3">
              <div>
                <label className="text-[length:var(--fs-caption)] text-[var(--c-text-secondary)]">
                  诉讼请求
                </label>
                <textarea
                  className="glass-input-field w-full h-16 px-3 py-2 rounded-lg text-[length:var(--fs-ui)] mt-1"
                  placeholder="如：判令被告支付违约金50万元..."
                  value={params.claims}
                  onChange={(e) => setParams(p => ({ ...p, claims: e.target.value }))}
                />
              </div>
              <div>
                <label className="text-[length:var(--fs-caption)] text-[var(--c-text-secondary)]">
                  事实与理由
                </label>
                <textarea
                  className="glass-input-field w-full h-24 px-3 py-2 rounded-lg text-[length:var(--fs-ui)] mt-1"
                  placeholder="描述案件事实与法律依据..."
                  value={params.facts_and_reasons}
                  onChange={(e) => setParams(p => ({ ...p, facts_and_reasons: e.target.value }))}
                />
              </div>
              <div>
                <label className="text-[length:var(--fs-caption)] text-[var(--c-text-secondary)]">
                  证据清单
                </label>
                <textarea
                  className="glass-input-field w-full h-16 px-3 py-2 rounded-lg text-[length:var(--fs-ui)] mt-1"
                  placeholder="如：1. 合同原件 2. 付款凭证 3. 往来邮件"
                  value={params.evidence_list}
                  onChange={(e) => setParams(p => ({ ...p, evidence_list: e.target.value }))}
                />
              </div>
            </div>
          </div>

          {/* 生成按钮 */}
          <PrimaryButton
            className="h-10 px-6 rounded-lg font-semibold inline-flex items-center gap-2"
            disabled={!selectedTemplate || loading}
            onClick={handleDraft}
          >
            <Sparkles size={16} />
            {loading ? 'AI 思考中...' : '生成文书'}
          </PrimaryButton>
        </div>
      </div>

      {/* 文书预览区 */}
      {document && (
        <div className="flex-1 overflow-y-auto px-8 py-6">
          <div className="max-w-[1200px] mx-auto">
            <div className="glass-card p-6">
              <div className="flex items-center gap-2 mb-4">
                <FileText size={16} className="text-[var(--c-accent)]" />
                <h2 className="text-[length:var(--fs-heading)] font-semibold text-[var(--c-text)]">
                  {document.title}
                </h2>
                <span className="text-[length:var(--fs-caption)] text-[var(--c-text-secondary)] ml-auto">
                  版本 {document.version}
                </span>
              </div>
              <div className="text-[length:var(--fs-body)] text-[var(--c-text)] whitespace-pre-wrap font-serif leading-relaxed">
                {document.content}
              </div>
            </div>

            {/* 操作栏 */}
            <div className="flex items-center gap-2 mt-4">
              <SecondaryButton className="h-9 px-4 rounded-lg inline-flex items-center gap-2"
                               onClick={handleCopy}>
                <Copy size={14} /> 复制
              </SecondaryButton>
              <SecondaryButton className="h-9 px-4 rounded-lg inline-flex items-center gap-2"
                               onClick={() => { /* 导出 .docx */ }}>
                <Download size={14} /> 导出Word
              </SecondaryButton>
              <SecondaryButton className="h-9 px-4 rounded-lg inline-flex items-center gap-2 ml-auto"
                               onClick={handleSendToChat}>
                <MessageSquare size={14} /> 发送到对话
              </SecondaryButton>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

declare function setActiveSection(s: string): void
declare function setPendingComposerPrefill(s: string): void
```

---

## 附录：legal-chineselaw 子域命令全量清单

| 子域 | 命令 | 工作流 | 映射功能 |
|---|---|---|---|
| **litigation-legal** | cold-start-interview, matter-intake, matter-briefing, matter-update, portfolio-status, matter-close, matter-workspace, demand-intake, demand-draft, demand-received, subpoena-triage, legal-hold, oc-status, claim-chart, chronology, deposition-prep, privilege-log-review, brief-section-drafter | litigation-demand-workflow, litigation-discovery-workflow, litigation-evidence-workflow, litigation-matter-workflow, litigation-trial-workflow | 案件管理 + 案件跟踪 + 文书起草 |
| **commercial-legal** | cold-start-interview, review, amendment-history, renewal-tracker, escalation-flagger, review-proposals, matter-workspace | commercial-renewal-workflow, commercial-review-workflow, nda-review, saas-msa-review, stakeholder-summary, vendor-agreement-review | 案件管理 + 文书起草 |
| **corporate-legal** | cold-start-interview, tabular-review, diligence-issue-extraction, material-contract-schedule, closing-checklist, written-consent, entity-compliance, integration-management, matter-workspace | ai-tool-handoff, board-minutes, corporate-ai-handoff-workflow, corporate-governance-workflow, corporate-ma-workflow, deal-team-summary, tabular-review | 案件管理 + 案件跟踪 |
| **employment-legal** | cold-start-interview, wage-hour-qa, hiring-review, termination-review, worker-classification, policy-drafting, leave-tracker, log-leave, investigation-open/add/memo/query/summary, expansion-kickoff/update, matter-workspace | employment-expansion-workflow, employment-hiring-workflow, employment-investigation-workflow, employment-leave-workflow, employment-policy-workflow, handbook-updates, internal-investigation, international-expansion | 案件管理 + 文书起草 |
| **ip-legal** | cold-start-interview, clearance, fto-triage, invention-intake, cease-desist, takedown, infringement-triage, ip-clause-review, oss-review, portfolio, matter-workspace | ip-contract-workflow, ip-patent-workflow, ip-portfolio-workflow, ip-trademark-workflow | 案件管理 + 文书起草 |
| **privacy-legal** | cold-start-interview, use-case-triage, pia-generation, dpa-review, dsar-response, reg-gap-analysis, policy-monitor, matter-workspace | privacy-compliance-workflow, privacy-policy-workflow | 案件管理 + 文书起草 |
| **regulatory-legal** | cold-start-interview, reg-feed-watcher, policy-diff, gaps, policy-redraft, comments, matter-workspace | gap-surfacer, regulatory-monitor-workflow, regulatory-policy-workflow | 案件跟踪 |
| **ai-governance-legal** | cold-start-interview, ai-inventory, use-case-triage, aia-generation, vendor-ai-review, reg-gap-analysis, policy-monitor, policy-starter, matter-workspace | aig-ai-governance-workflow | 案件管理 |
| **product-legal** | cold-start-interview, is-this-a-problem, launch-review, marketing-claims-review, matter-workspace | feature-risk-assessment, product-compliance-workflow | 案件跟踪 |
| **legal-clinic** | cold-start-interview, build-guide, ramp, client-intake, client-comms-log, research-start, memo, draft, client-letter, status, deadlines, supervisor-review-queue, semester-handoff | clinic-case-workflow, clinic-client-workflow, clinic-management-workflow, clinic-supervisor-workflow | 案件管理 + 文书起草 |
| **law-student** | cold-start-interview, socratic-drill, case-brief, outline-builder, irac-practice, legal-writing, cold-call-prep, bar-prep-questions, flashcards, exam-forecast, study-plan, session | law-student-practice-workflow, law-student-session-workflow | —（学习辅助） |
| **legal-builder-hub** | cold-start-interview, registry-browser, skill-installer, skills-qa, auto-updater, disable, uninstall | builder-hub-management-workflow, related-skills-surfacer, skill-manager | —（生态工具） |
