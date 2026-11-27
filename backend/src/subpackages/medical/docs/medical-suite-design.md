# medical-suite 设计方案

> 状态：**历史设计稿**（基于已移除的 `.prismclaw/plugins/` 插件结构）。
> 当前实现位于 `backend/.prismclaw/experts/`，概念与目录说明见
> [专家体系](../../../../../docs/architecture/modules/03-expert-system.md)。
> 范围：`backend/.prismclaw/plugins/medical-suite/` 的插件结构、专家、技能与配套能力。
> 本文只描述最终形态，不记录迭代过程。

---

## 1. 定位

`medical-suite` 是 PrismClaw 的医疗专家技能套件，由 5 个子插件组成，覆盖四类工作：临床诊疗、医学科研、机构治理与运营、个人健康管理。

设计目标不是"功能齐全"，而是**只保留模型自身不可靠、或出错代价高的能力**：

- 模型不可靠：需要脚本、码表、外部 API、固定字段契约。
- 代价高：患者安全、合规责任、证据质量、消费者免责。

---

## 2. 设计判据

### 2.1 子插件独立的条件

三条同时满足才拆为独立子插件：

1. **专属工具面**：有独立 MCP、hooks 或运行时依赖。
2. **专属风险闸门**：安全、合规、PHI、免责等级不同，需要独立的 `PLUGIN.md` 与 hooks。
3. **独立可移植性**：用户可能只安装其中一部分。

任一条不满足，并入已有子插件。

### 2.2 技能成立的条件

至少命中一条，且维护成本低于收益：

1. **确定性资产**：脚本、码表、外部 API。
2. **硬输出契约**：字段或结构必须固定。
3. **安全闸门**：需要 hook 或强制核查。
4. **高价值知识资产**：references 显著提升可靠性。

反例：翻译一段文字、写一段科普、复盘一个病例、查询一次试验。这些由主模型调用工具即可完成，不单独建技能。

### 2.3 专家成立的条件

需要**独立工具白名单与独立上下文**，用于隔离风险或控制长流程。否则由主 Agent 直接调用技能。

---

## 3. 执行机制约束

以下为 PrismClaw 插件加载与执行的既有机制，是设计的前提。

### 3.1 技能三层披露

- 第一层：项目技能逐个列名；**插件技能只暴露"包名 + 描述 + 技能数"**，具体技能不进入系统提示词。
- 第二层：`skill_search` 按查询检索候选。
- 第三层：`activate_skill` 加载 SKILL.md 正文，并解锁该技能的 `allowed_tools`。

因此技能数量直接影响检索竞争。同类能力必须合并，命名必须可区分。

### 3.2 专家发现

插件专家的名称同样不进入固定提示词，由 `subagent_search` 检索、`sub_agent` 派发。专家数量过多会稀释召回，并增加描述维护成本。

### 3.3 PLUGIN.md 注入预算

`PLUGIN.md` 作为插件指令注入，子插件级优先于套件级。默认预算约 2000 token，超限时按关键词优先保留"安全、禁止、输出、格式"类章节，其余截断。

约束：`PLUGIN.md` 只放红线、免责、输出规范；长规范、字段表、法规清单放入技能 `references/`。

### 3.4 目录自动扫描

`skills/` 与 `agents/` 即使不在 `plugin.json` 中声明也会被扫描。声明路径属于冗余，可省略。

单插件技能上限为 100，候选上限 500。

### 3.5 其他事实

- `plugin.json` 不支持 `output_styles` 字段；`output-styles/` 目录会被加载为资产，但不会自动注入提示词。
- `commands/` 目录中的命令**不带命名空间**，命令名必须全局唯一。
- hooks 支持 `PreToolUse`、`PostToolUse` 等事件，可拦截、改写输入、追加上下文。
- MCP 配置支持 `${PRISMCLAW_PLUGIN_ROOT}`、`${PRISMCLAW_PLUGIN_DATA}`、`${PRISMCLAW_MARKETPLACE_DATA}` 变量。

---

## 4. 总体结构

```
backend/.prismclaw/plugins/medical-suite/
├── .plugin/plugin.json                  # marketplace 清单
├── PLUGIN.md                            # 套件级医疗纪律
├── safety/                              # 共享安全脚本
│   ├── confidence_scorer.py
│   └── claim_verifier.py
│
├── medical-core/                        # 能力底座
│   ├── .plugin/plugin.json
│   ├── PLUGIN.md
│   ├── mcp.json                         # medical-retrieval + FHIR
│   ├── mcp_servers/
│   │   ├── medical-retrieval/
│   │   └── fhir/
│   └── skills/
│       ├── doc-extract/
│       └── fhir/
│
├── medical-clinical/                    # 诊疗
│   ├── .plugin/plugin.json
│   ├── PLUGIN.md
│   ├── agents/clinical-expert.md
│   ├── commands/{clinical-summary,drug-check}.md
│   ├── hooks/hooks.json
│   └── skills/
│       ├── clinical/clinical-decision-support/
│       ├── clinical/clinical-note/
│       ├── clinical/prescription-safety/
│       ├── coding/medical-coding/
│       └── communication/patient-education/
│
├── medical-research/                    # 科研
│   ├── .plugin/plugin.json
│   ├── PLUGIN.md
│   ├── agents/{evidence-expert,analysis-expert}.md
│   ├── commands/{literature-search,trial-search,stats-analyze}.md
│   ├── hooks/hooks.json
│   └── skills/
│       ├── evidence/medical-literature-review/
│       ├── evidence/deepsearch/
│       ├── design/study-design/
│       ├── statistics/biomedical-statistics/
│       ├── writing/academic-writing/
│       └── writing/medical-ppt/
│
├── medical-governance/                  # 合规与运营
│   ├── .plugin/plugin.json
│   ├── PLUGIN.md
│   ├── agents/compliance-expert.md
│   ├── commands/{compliance-audit,prior-auth-packet}.md
│   ├── hooks/hooks.json
│   └── skills/
│       ├── compliance/privacy-compliance/
│       ├── compliance/prior-auth/
│       └── operations/claims-workflow/
│
└── medical-wellness/                    # 健康管理
    ├── .plugin/plugin.json
    ├── PLUGIN.md
    ├── agents/wellness-expert.md
    ├── commands/wellness-report.md
    └── skills/
        ├── health-record-analysis/
        └── emergency-card/
```

### 4.1 套件清单

```json
{
  "name": "medical-suite",
  "version": "0.1.0",
  "description": "面向临床、科研、机构治理与健康管理的医疗专家技能套件",
  "plugins": [
    {"name": "medical-core", "source": "./medical-core"},
    {"name": "medical-clinical", "source": "./medical-clinical"},
    {"name": "medical-research", "source": "./medical-research"},
    {"name": "medical-governance", "source": "./medical-governance"},
    {"name": "medical-wellness", "source": "./medical-wellness"}
  ]
}
```

### 4.2 子插件清单

```json
{
  "name": "medical-clinical",
  "version": "0.1.0",
  "description": "临床诊疗：决策支持、病历、用药安全、编码与患者沟通",
  "author": {"name": "PrismClaw"},
  "commands": "./commands",
  "hooks": "./hooks/hooks.json",
  "dependencies": ["medical-core"]
}
```

`skills` 与 `agents` 由目录自动扫描，不声明。

---

## 5. 子插件设计

### 5.1 medical-core

**存在理由**：命中判据 1（自建检索 MCP、FHIR MCP、文档解析）与判据 3（可独立升级）。MCP 必须只声明一次，避免多个子插件重复连接与重复鉴权。

**专家**：无。

**技能**

| 技能 | 判据 | 职责 | 输入 → 输出 |
|---|---|---|---|
| doc-extract | 1、4 | 解析 PDF/DOCX/XLSX/PPTX/RTF，抽取医学实体 | 文件 → 文本 / 结构化 JSON |
| fhir | 1、2 | SMART 登录、拉取患者与文档，映射为统一结构 | 连接 → 患者数据 |

`fhir` 只做数据接入与结构映射，不做临床解释。

**MCP**

- `medical-retrieval`：自建，stdio，见第 9 节。
- `FHIR`：默认 `enabled: false`，需机构注册与交互登录。

**命令**：无。

**hooks**：无。

---

### 5.2 medical-clinical

**存在理由**：命中判据 1（检索 + 写 + 处方 hook）、判据 2（患者安全，免责与复核要求最高）、判据 3（可单独装给医生）。

**专家**

| 专家 | 职责 | 工具边界 |
|---|---|---|
| clinical-expert | 鉴别诊断、治疗方案、处方审查、病历、编码 | `read,grep,glob,activate_skill,medical-retrieval:*,present_files` |

**技能**

| 技能 | 判据 | 职责 | 输入 → 输出 |
|---|---|---|---|
| clinical-decision-support | 3、4 | 鉴别诊断、检查与用药建议、红旗信号；治疗方案作为其输出 | 主诉 + 检查 → 决策要点 |
| clinical-note | 2 | 生成规范病历；反向抽取结构化字段 | 素材 ↔ 病历 / JSON |
| prescription-safety | 1、3 | 剂量、过敏、相互作用核查 | 处方 → 风险清单 |
| medical-coding | 1 | ICD-10 与操作编码 | 诊断/操作 → 编码 |
| patient-education | 2 | 将专业结论转为患者可读材料 | 结论 → 科普稿 |

`clinical-note` 同时覆盖生成与抽取，避免同一份记录的字段在两个技能间漂移。
`medical-coding` 合并诊断编码与操作编码，共用同一份码表资产。

**命令**

- `clinical-summary`：汇总患者信息，输出结构化诊疗摘要。
- `drug-check`：对当前处方执行用药安全核查。

**hooks**

`PostToolUse` matcher `prescription-safety|clinical-decision-support`，调用 `safety/confidence_scorer.py`，对高风险结论补充置信度提示。

---

### 5.3 medical-research

**存在理由**：命中判据 1（bash 脚本、检索、文档生成）、判据 2（证据质量与可复现性）、判据 3（科研用户可独立安装）。临床域的风险是患者安全，科研域的风险是证据与可复现性，两者不能共用闸门。

**专家**

| 专家 | 职责 | 工具边界 |
|---|---|---|
| evidence-expert | 循证问答、文献综述、研究设计 | `read,grep,activate_skill,medical-retrieval:*,write,present_files` |
| analysis-expert | 统计分析与科研绘图 | `bash,read,write,activate_skill,present_files` |

写作类任务不设专家，使用技能的 `execution_mode: subagent` 在独立上下文执行。

**技能**

| 技能 | 判据 | 职责 | 输入 → 输出 |
|---|---|---|---|
| medical-literature-review | 1、4 | PRISMA 流程、检索式、证据整合 | 主题 → 综述 |
| deepsearch | 1、4 | 多源证据聚合，输出带引用的循证答案 | 问题 → 证据答案 |
| study-design | 2、4 | 试验方案、样本量论证 | 假设 → 方案 |
| biomedical-statistics | 1、4 | 检验选择、统计脚本、科研绘图 | 数据 → 统计报告与图 |
| academic-writing | 2 | 论文 IMRaD 与基金标书 | 素材 → 稿件 / 标书 |
| medical-ppt | 1 | 生成 pptx 学术汇报 | 内容 → 幻灯片 |

`biomedical-statistics` 合并统计与绘图，因为二者共用同一次分析与同一组脚本。
`academic-writing` 合并论文与标书，二者共用大纲与引用规范。

**命令**

- `literature-search`：执行检索并返回候选文献。
- `trial-search`：查询 ClinicalTrials.gov。
- `stats-analyze`：对给定数据执行统计分析。

**hooks**

`PostToolUse` matcher `biomedical-statistics|medical-literature-review`，调用 `safety/confidence_scorer.py`。

---

### 5.4 medical-governance

**存在理由**：命中判据 1（法规检索 + claim hook）、判据 2（合规与法务责任独立）、判据 3（机构端单独采购）。错误类型是合规与法律风险，与诊疗、科研的风险模型不同。

**专家**

| 专家 | 职责 | 工具边界 |
|---|---|---|
| compliance-expert | 合规审查、隐私、预授权、理赔核查 | `read,grep,activate_skill,medical-retrieval:*` |

**技能**

| 技能 | 判据 | 职责 | 输入 → 输出 |
|---|---|---|---|
| privacy-compliance | 2、4 | HIPAA 与数据隐私评估、脱敏建议 | 场景 → 合规报告 |
| prior-auth | 2 | 预授权材料与流程 | 病例 → 申请包 |
| claims-workflow | 2、3 | 理赔流程、材料校验、欺诈风险标记 | 单据 → 理赔方案 |

`privacy-compliance` 合并 HIPAA 与数据隐私，二者共用法规检索与脱敏判断。
`claims-workflow` 内含欺诈核查步骤，不单独建技能。

**命令**

- `compliance-audit`：对给定流程执行合规审查。
- `prior-auth-packet`：生成预授权申请包。

**hooks**

- `PreToolUse` matcher `claims-workflow|prior-auth`，调用 `safety/claim_verifier.py`。
- `PostToolUse` matcher 同上，调用 `safety/confidence_scorer.py`。

---

### 5.5 medical-wellness

**存在理由**：命中判据 2（消费者免责与危机转介）与判据 3（可单独装给非医生用户）。工具面较弱，但免责与转介要求与临床域不同，不能共用医生人设。

**专家**

| 专家 | 职责 | 工具边界 |
|---|---|---|
| wellness-expert | 睡眠、营养、运动、减重、康复、心理、体质、家族史分析 | `read,activate_skill,present_files` |

**技能**

| 技能 | 判据 | 职责 | 输入 → 输出 |
|---|---|---|---|
| health-record-analysis | 2、3、4 | 统一的健康记录分析：摄入记录 → 计算指标 → 输出报告 | 记录 → 评估报告 |
| emergency-card | 2 | 生成固定格式急救卡 | 健康档案 → 急救卡 |

`health-record-analysis` 以模式区分睡眠、营养、运动、减重、康复、心理、体质、家族史。这八类共用同一套"摄入 → 指标 → 报告"流程，拆成多个技能只会复制同一逻辑。

**命令**

- `wellness-report`：生成个人健康分析报告。

**hooks**：无。

---

## 6. 技能总表

共 18 个技能。

| 子插件 | 技能 | 判据 |
|---|---|---|
| medical-core | doc-extract | 1、4 |
| medical-core | fhir | 1、2 |
| medical-clinical | clinical-decision-support | 3、4 |
| medical-clinical | clinical-note | 2 |
| medical-clinical | prescription-safety | 1、3 |
| medical-clinical | medical-coding | 1 |
| medical-clinical | patient-education | 2 |
| medical-research | medical-literature-review | 1、4 |
| medical-research | deepsearch | 1、4 |
| medical-research | study-design | 2、4 |
| medical-research | biomedical-statistics | 1、4 |
| medical-research | academic-writing | 2 |
| medical-research | medical-ppt | 1 |
| medical-governance | privacy-compliance | 2、4 |
| medical-governance | prior-auth | 2 |
| medical-governance | claims-workflow | 2、3 |
| medical-wellness | health-record-analysis | 2、3、4 |
| medical-wellness | emergency-card | 2 |

---

## 7. 专家总表

共 5 个。

| 专家 | 所属子插件 | 隔离目的 |
|---|---|---|
| clinical-expert | medical-clinical | 诊疗判断与检索，避免写权限越界 |
| evidence-expert | medical-research | 长检索流程隔离，避免污染主上下文 |
| analysis-expert | medical-research | 脚本执行隔离，保证统计口径可核 |
| compliance-expert | medical-governance | 只读法规检索，隔离法律口径 |
| wellness-expert | medical-wellness | 消费者免责与危机转介，独立于医生人设 |

写作、PPT、编码不单独设专家，由主 Agent 调用技能或使用技能的 `execution_mode: subagent`。

---

## 8. 能力契约

技能正文只写契约名，不写具体宿主工具名。

| 契约 | 提供方 | 用途 |
|---|---|---|
| `activate_skill` | 内置 | 激活技能 |
| `present_files` | 内置 | 交付产物 |
| `document.read` | 文档 MCP | 读取 PDF / DOCX / XLSX / PPTX |
| `document.write` | 文档 MCP | 生成 DOCX / PPTX / XLSX |
| `image.read` / `image.generate` | 图像 MCP | 图像读取与生成 |
| `kb_search` | medical-retrieval | 药品、指南、疾病、法规统一检索 |
| `literature_search` | medical-retrieval | PubMed / Europe PMC / Crossref |
| `drug_label_lookup` | medical-retrieval | 药品说明书 |
| `guideline_search` | medical-retrieval | 临床指南 |
| `regulation_search` | medical-retrieval | 法规 |
| `trial_search` | medical-retrieval | 临床试验 |
| `web.search` / `web.fetch` | web MCP | 公网补充检索 |

---

## 9. medical-retrieval MCP

### 9.1 定位

替代收费且不可用的第三方医学检索服务，为临床、科研、治理三个子插件提供统一的免费检索入口。

### 9.2 实现

- 位置：`medical-core/mcp_servers/medical-retrieval/`
- 语言：Python，stdio 传输
- 检索：SQLite FTS5
- 缓存：本地 SQLite，带 TTL
- 返回结构：`{title, source, year, url, snippet, license}`

### 9.3 工具

| 工具 | 说明 |
|---|---|
| `kb_search` | 按 scope 检索 drug / guideline / disease / regulation |
| `literature_search` | 检索文献元数据 |
| `drug_label_lookup` | 查询药品说明书 |
| `guideline_search` | 查询临床指南 |
| `regulation_search` | 查询法规 |
| `trial_search` | 查询临床试验 |
| `status` | 返回索引、缓存与数据源健康状态 |

### 9.4 数据源

| 能力 | 数据源 | 许可 |
|---|---|---|
| 文献 | PubMed E-utilities、Europe PMC、Crossref | 免费，元数据开放 |
| 临床试验 | ClinicalTrials.gov API v2 | 免费 |
| 药品 | openFDA、RxNorm | 免费 |
| 编码 | WHO ICD-10、CDC ICD-10-CM | 免费 |
| 法规 | 国家法律法规数据库 | 公开 |
| 指南 | WHO / NICE / 卫健委公开指南 | 公开，需核对版权 |
| 疾病 | MedlinePlus、Orphanet | 免费 |

每条结果必须携带 `license` 字段。来源不明或条款不允许再分发的内容不进入索引。

### 9.5 配置

```json
{
  "mcpServers": {
    "medical-retrieval": {
      "command": "python3",
      "args": ["${PRISMCLAW_PLUGIN_ROOT}/mcp_servers/medical-retrieval/server.py"],
      "env": {
        "MEDICAL_CACHE_DIR": "${PRISMCLAW_PLUGIN_DATA}/cache"
      }
    }
  }
}
```

---

## 10. 阿福技能迁移规则

1. `metadata.displayName` 映射为 `name_zh`，`metadata.category` 映射为中文 `category`。
2. 正文工具名按第 8 节契约替换。
3. 删除 `manifest.json`、`recommended-queries.json`、`.doctorbuddy-local-featured`。
4. 正文中"按上方纪律"等悬空引用删除，或改指 `PLUGIN.md`。
5. `references/`、`scripts/`、`tests/` 原样保留，脚本解释器统一为 `python3`。
6. `allowed_tools` 按技能职责收窄，不继承宿主全量工具。
7. `description` 精简为"触发场景 + 边界"。

---

## 11. 前置依赖

以下加载器问题会影响本方案落地，需先处理。

| 问题 | 位置 | 影响 | 最小改动 |
|---|---|---|---|
| 跨源同名插件未去重 | `loading/merge.py` | 同名子插件可能同时进入合并结果，缺少冲突告警 | 合并后按 name 去重，保留高优先级，并记录 warning |
| `commands` 路径声明被忽略 | `loading/_declarative_load.py` | `manifest.commands` 的字符串或数组形式不生效 | 统一使用 `_resolve_relative_paths` |
| MCP 的 env 不解析 `user_config` | `loading/_declarative_load.py` | `mcp.json` 中的 `${user_config.KEY}` 不生效 | 在 MCP 配置替换中补 `substitute_user_config_in_content` |
| shared hooks 使用 `python` | `safety/` 与各 `hooks.json` | macOS 可能无 `python` 别名 | 统一为 `python3` |

---

## 12. 落地分期

### P0 基础设施

- 修复第 11 节中的合并去重与 MCP `user_config` 两项。
- 创建 `medical-suite` 骨架、套件 `PLUGIN.md`、`medical-core`。
- 实现 `medical-retrieval` 最小版本：文献、试验、药品三类免费 API + FTS 缓存。

### P1 临床域

- `medical-core`：`doc-extract`、`fhir`。
- `medical-clinical`：5 个技能、`clinical-expert`、2 个命令、处方安全 hook。

### P2 科研域

- `medical-research`：6 个技能、2 个专家、3 个命令、统计与综述 hook。
- 依赖 `medical-retrieval` 已就绪的文献与试验检索。

### P3 治理与健康

- `medical-governance`：3 个技能、1 个专家、2 个命令、理赔核查 hook。
- `medical-wellness`：2 个技能、1 个专家、1 个命令。

### P4 收尾

- 清理旧插件包。
- 核对阿福技能许可。
- 执行端到端验收。

---

## 13. 验收标准

1. 所有 SKILL.md 通过解析校验，`name`、`description`、`category` 齐全。
2. 所有专家与技能的 `allowed_tools` 均指向已注册工具，无未注册项。
3. `mcp.json` 不含虚构包，所有 `command` 可在目标平台解析。
4. 全仓检索无悬空工具名。
5. 各子插件技能数不超过 100。
6. `PLUGIN.md` 注入不超预算，超限时安全类章节保留。
7. 命令名全局唯一。
8. 端到端链路跑通：`literature-search` → `evidence-expert` → `present_files`。
9. hooks 在 macOS 下以 `python3` 正常执行。

---

## 14. 风险与未决

| 风险 | 说明 | 处置 |
|---|---|---|
| 阿福技能许可未定 | 部分技能无 LICENSE，含公司内部内容 | 对外分发前取得授权；否则仅本地私有使用 |
| 中文权威语料缺口 | 免费源无法完全替代商业中文临床知识库 | 结果标注来源与证据等级；不承诺等同商业库 |
| 合并去重的下游语义 | 覆盖还是 first-match 取决于注册实现 | P0 阶段先确认再改 |
| MCP `user_config` 未生效 | 敏感 key 无法从 keychain 注入 MCP | 与第 11 节一并修复 |
| 数据许可 | "公开"不等于"可再分发" | 逐项记录 `license`，不合规不入索引 |
| 命令命名冲突 | `commands/` 目录形式不带命名空间 | 命令名统一加域前缀 |

---

## 15. 模板

### 15.1 技能

```yaml
---
name: clinical-decision-support
name_zh: 临床决策支持
description: 基于主诉、检查与指南给出鉴别诊断、检查与用药建议，并标出红旗信号。触发：鉴别诊断、治疗方案、用药建议。
category: 临床诊疗
invocation_policy: auto
execution_mode: inline
priority: 80
allowed_tools:
  - read
  - grep
  - medical-retrieval:*
  - present_files
---
```

### 15.2 专家

```yaml
---
name: clinical-expert
description: 临床诊疗专家，负责鉴别诊断、治疗方案、处方审查与病历生成。
allowed_tools:
  - read
  - grep
  - glob
  - activate_skill
  - medical-retrieval:*
  - present_files
preload_skills:
  - clinical-decision-support
  - prescription-safety
---
```

`model` 不写死，由运行时配置注入。

### 15.3 命令

```yaml
---
name: clinical-summary
description: 汇总当前患者信息，输出结构化诊疗摘要
argument_hint: "<患者标识或病历路径>"
allowed_tools: [read, grep, activate_skill, medical-retrieval:*]
---
```

### 15.4 hooks

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "prescription-safety|clinical-decision-support",
        "hooks": [
          {
            "type": "command",
            "command": "python3 ${PRISMCLAW_PLUGIN_ROOT}/../safety/confidence_scorer.py --stdin",
            "timeout": 5.0
          }
        ]
      }
    ]
  }
}
```
