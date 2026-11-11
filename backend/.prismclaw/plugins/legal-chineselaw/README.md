# PrismClaw for Legal 中国法律改造版

> 本仓库是 `legal-chineselaw` 的中文翻译版，便于中文法律工作场景阅读、学习和二次适配。
>
> - 原项目链接：[https://github.com/anthropics/legal-chineselaw](https://github.com/anthropics/legal-chineselaw)
> - 原作者：Anthropic PBC
> - 许可证：Apache License 2.0
> - 翻译者：OpenAI Codex
>
> 原项目版权、许可证及上游更新归 Anthropic PBC 及原贡献者所有；本仓库仅对文档、提示词、skill 说明等内容进行中文本地化整理。

## 版本说明

`legal-chineselaw` 是 PrismClaw for Legal 的单纯汉化版，适合下载后根据自己的法律服务场景继续改造。

`legal-chineselaw-chineselaw` 是在汉化基础上进一步改造过的中国法律场景版本，可直接下载和使用。

面向最常见法律工作流的参考 agent、skill 和数据连接器——涵盖内部商务、隐私、产品、公司、劳动、诉讼、监管、AI 治理、IP，以及法律学习领域（法学院诊所与学生）。

> **初次使用？** 请先阅读 [QUICKSTART.md](QUICKSTART.md)——60 秒完成安装。本 README 为完整参考文档。

本仓库的所有内容均支持**两种使用方式，共用同一套源码**：可作为 [PrismClaw Cowork](https://prismclaw.ai) 或 [PrismClaw](https://prismclaw.ai) plugin 安装，也可通过 [PrismClaw Managed Agents API](https://docs.prismclaw.ai) 部署在您自己的工作流引擎后端。相同的系统提示、相同的 skill——您选择运行位置。

## 在 Cowork 中快速上手
- [安装 PrismClaw Desktop](https://prismclaw.ai/download)
- 获取 PrismClaw Cowork 访问权限
- 按照下方视频中的说明操作：

https://github.com/user-attachments/assets/51394f0a-5277-4fe2-b81c-5c5e9ac876b5

> [!IMPORTANT]
> **这些 plugin 的所有输出均为供律师审阅的草稿——不构成法律意见、不构成法律结论、不替代律师服务。** 其内置的安全机制正是基于此：每处引用均标注来源、特权与主观法律判断采用保守默认值、管辖权假设明确呈现，任何文件在提交、发送或被依赖前均设有明确的确认关卡。律师负责审阅、核实，并对离开办公室的任何内容承担职业责任。这些 plugin 让审阅工作更快；它们不替代审阅本身。
>
> **这些 plugin 不代表 Anthropic 的法律立场。** 它们是帮助律师分析问题的工具。skill 中包含的清单项目、建议框架、风险标记，或对判例法及监管指引的描述，均是对审阅律师独立分析的辅助，而非 Anthropic 对法律的看法。本领域许多法律问题尚未定论且持续演变。使用 plugin 的律师——而非 plugin 本身，也非 Anthropic——对其工作成果中所持法律立场负责。

仓库内容：

- **执业领域 plugin**：涵盖内部法务、律所及学术法律工作——每个 plugin 均围绕一个"冷启动访谈"构建，该访谈会学习您的工作方式，并写入一份供所有 skill 读取的 `PRISMCLAW.md` 执业档案。
- **Managed agent cookbook**：用于定时执行、持续监测的工作流（续约监控、案件监控、监管动态监控、尽调网格、发布雷达）。
- **MCP 连接器**：覆盖通用生产力工具（Slack、Google Drive、Box）及法律专用系统（Ironclad、DocuSign、iManage、Everlaw、CourtListener 等）。
- **[命名 agent](#agents)**——端到端工作流 agent（供应商协议审阅者、DSAR 响应者、终止审阅者、权利要求图表构建者……），每个有对应名称和单条运行命令。

## Agents

每个 agent 以其负责的工作流命名。它们是最常用的入口——先从与您工作匹配的 agent 开始，再根据团队实际情况调整底层 skill、执业档案和连接器。

| Agent | 功能说明 | Plugin | 命令 |
|---|---|---|---|
| **供应商协议审阅者** | 依据您的 playbook 审阅供应商 MSA 并生成批注备忘录 | `commercial-legal` | `/commercial-legal:review` |
| **NDA 分级员** | 对入站 NDA 进行绿/黄/红分级，只有疑难件才需律师审阅 | `commercial-legal` | `/commercial-legal:review` |
| **修订追踪器** | 追踪合同在基础协议及各修订版本间的变化 | `commercial-legal` | `/commercial-legal:amendment-history` |
| **续约监控器** | 扫描合同登记册中的取消截止日和续约截止日 | `commercial-legal` | 定时 agent |
| **交易复盘** | 每周扫描已签协议中的 playbook 偏差——趁记忆犹新提示律师记录背景信息 | `commercial-legal` | 定时 agent |
| **Playbook 监控器** | 监测偏差日志，当条款持续偏移时提出 playbook 更新建议 | `commercial-legal` | 定时 agent |
| **升级路由器** | 将合同问题路由至正确的审批人并起草请示 | `commercial-legal` | `/commercial-legal:escalation-flagger` |
| **表格式尽调审阅** | 对数据室进行表格式审阅，每文件一行、每格引用来源 | `corporate-legal` | `/corporate-legal:tabular-review` |
| **问题提取器** | 读取 VDR 文件，按内部分类和重要性阈值提取问题 | `corporate-legal` | `/corporate-legal:diligence-issue-extraction` |
| **董事会决议起草者** | 以内部格式起草全体书面同意决议，并检索先例 | `corporate-legal` | `/corporate-legal:written-consent` |
| **重大合同附表构建器** | 根据尽调发现对照收购协议阈值构建披露附表 | `corporate-legal` | `/corporate-legal:material-contract-schedule` |
| **主体合规追踪器** | 跨管辖区和主体类型计算申报截止日，执行健康审计 | `corporate-legal` | `/corporate-legal:entity-compliance` |
| **交割清单驱动器** | 追踪阻碍交割的每项条件、同意、文件及申报 | `corporate-legal` | `/corporate-legal:closing-checklist` |
| **整合操作手册** | 分阶段的交割后整合计划，含同意追踪和每周状态 | `corporate-legal` | `/corporate-legal:integration-management` |
| **数据室监控器** | 监控 VDR 新上传内容，按计划发布交割清单状态 | `corporate-legal` | 定时 agent |
| **终止审阅者** | 针对特定管辖区风险标记审查拟议终止方案 | `employment-legal` | `/employment-legal:termination-review` |
| **入职审阅者** | 审查 offer 信函和竞业限制条款，含管辖区检查 | `employment-legal` | `/employment-legal:hiring-review` |
| **劳动者分类筛查器** | 依据控制性州法律测试对拟议用工关系进行分类 | `employment-legal` | `/employment-legal:worker-classification` |
| **假期追踪器** | 监控未结假期及 FMLA/CFRA/PFL/ADA 截止日和决策节点提醒 | `employment-legal` | 定时 agent |
| **调查负责人** | 开启、追踪、补充和汇总内部调查事项 | `employment-legal` | `/employment-legal:investigation-open` |
| **政策起草人** | 起草劳动政策，在法律存在差异的州附加补充条款 | `employment-legal` | `/employment-legal:policy-drafting` |
| **国际扩张规划师** | 启动新国家的 EOR vs. 自建主体规划及境外律师简报 | `employment-legal` | `/employment-legal:expansion-kickoff` |
| **工资与工时问答** | 支持管辖区的劳动问答，适用于"快速提问"频道 | `employment-legal` | `/employment-legal:wage-hour-qa` |
| **DSAR 响应者** | 在法定时限内起草 DSAR 确认函和实质性回复 | `privacy-legal` | `/privacy-legal:dsar-response` |
| **DPA 审阅者** | 以控制方或处理方身份依据 playbook 审阅 DPA | `privacy-legal` | `/privacy-legal:dpa-review` |
| **PIA 生成器** | 以内部格式为新功能或活动生成隐私影响评估 | `privacy-legal` | `/privacy-legal:pia-generation` |
| **隐私分级员** | 判断处理活动是否需要 PIA、强制性 GDPR DPIA，或可直接推进 | `privacy-legal` | `/privacy-legal:use-case-triage` |
| **隐私法规差距检查器** | 将新法规或修订法规与当前隐私政策和实践进行比对 | `privacy-legal` | `/privacy-legal:reg-gap-analysis` |
| **隐私政策监控器** | 扫描已保存的 PIA、DPA 审阅和分级结果，监测政策偏移 | `privacy-legal` | `/privacy-legal:policy-monitor` |
| **发布审阅者** | 依据您的风险标定审阅产品发布 | `product-legal` | `/product-legal:launch-review` |
| **营销声明检查器** | 标记需要实证支撑、重新表述或删除的文案 | `product-legal` | `/product-legal:marketing-claims-review` |
| **"这是个问题吗？"分级** | 针对 Slack 快速提问的快速解答——与您的风险标定进行模式匹配 | `product-legal` | `/product-legal:is-this-a-problem` |
| **发布监控器** | 监控发布追踪器中即将需要法律审阅的发布 | `product-legal` | 定时 agent |
| **监管动态监控器** | 轮询监管动态并撰写周一早间摘要 | `regulatory-legal` | 定时 agent |
| **按需监管检查** | 立即检查监管动态并报告上次检查以来的新内容 | `regulatory-legal` | `/regulatory-legal:reg-feed-watcher` |
| **政策差异对比** | 将特定监管变化与已索引的政策库进行比对 | `regulatory-legal` | `/regulatory-legal:policy-diff` |
| **差距追踪器** | 未结差距追踪——已标记但尚未弥合的内容 | `regulatory-legal` | `/regulatory-legal:gaps` |
| **政策重起草** | 针对差距的带标注政策重起草——供政策负责人审阅的提案，不直接编辑源文件 | `regulatory-legal` | `/regulatory-legal:policy-redraft` |
| **NPRM 评论追踪器** | 审阅未结 NPRM 评论期，记录决策，追踪截止日 | `regulatory-legal` | `/regulatory-legal:comments` |
| **AI 用例分级员** | 依据您的注册表对拟议 AI 用例进行分类 | `ai-governance-legal` | `/ai-governance-legal:use-case-triage` |
| **AI 影响评估员** | 跨适用监管框架执行 AI 影响评估 | `ai-governance-legal` | `/ai-governance-legal:aia-generation` |
| **供应商 AI 审阅者** | 审阅供应商 AI 条款中的数据训练、责任、模型变更和政策差距 | `ai-governance-legal` | `/ai-governance-legal:vendor-ai-review` |
| **AI 法规差距检查器** | 将新 AI 法规与当前治理状态进行比对 | `ai-governance-legal` | `/ai-governance-legal:reg-gap-analysis` |
| **AI 政策监控器** | 扫描已保存的 AIA、分级结果和供应商审阅，监测 AI 政策偏移 | `ai-governance-legal` | `/ai-governance-legal:policy-monitor` |
| **商标清查筛查器** | 首轮清查，含淘汰检查和混淆启发式分析 | `ip-legal` | `/ip-legal:clearance` |
| **停止侵权函起草者** | 根据您的执法立场起草或分级 C&D | `ip-legal` | `/ip-legal:cease-desist` |
| **DMCA 下架通知** | 起草下架通知、分级收到的通知，或起草 §512(g) 反通知 | `ip-legal` | `/ip-legal:takedown` |
| **开源合规检查器** | 依据您的部署模型对开源许可证进行分类 | `ip-legal` | `/ip-legal:oss-review` |
| **自由实施分级员** | 对潜在阻碍专利进行结构化初步审查——分级，非正式意见 | `ip-legal` | `/ip-legal:fto-triage` |
| **侵权分级员** | 跨商标/著作权/专利/商业秘密分级——分析因素，非最终结论 | `ip-legal` | `/ip-legal:infringement-triage` |
| **IP 条款审阅者** | 审阅转让、所有权、许可授予、保证和赔偿条款 | `ip-legal` | `/ip-legal:ip-clause-review` |
| **IP 组合追踪器** | 注册、续期、维护费、使用声明 | `ip-legal` | `/ip-legal:portfolio` |
| **IP 续期监控器** | 来自 IP 组合登记册的定时截止日报告 | `ip-legal` | 定时 agent |
| **权利要求图表构建器** | 逐要素权利要求图表，专利或民事诉因 | `litigation-legal` | `/litigation-legal:claim-chart` |
| **案件监控器** | 监控法院案件的申报和截止日 | `litigation-legal` | 定时 agent |
| **催告函起草者** | 起草含 FRE 408 意识和发送关卡的催告函 | `litigation-legal` | `/litigation-legal:demand-draft` |
| **催告函接收** | 起草前背景收集——当事方、事实、依据、筹码、特权 | `litigation-legal` | `/litigation-legal:demand-intake` |
| **收到催告函分级** | 对收到的催告函进行分级——选项、组合交叉检查、交接 | `litigation-legal` | `/litigation-legal:demand-received` |
| **传票分级** | 对新传票进行分类、确定范围并规划合规方案 | `litigation-legal` | `/litigation-legal:subpoena-triage` |
| **时间线构建器** | 从已声明来源和上传文件中构建或更新时间线 | `litigation-legal` | `/litigation-legal:chronology` |
| **庭前证人准备** | 构建与案件理论挂钩的证人询问提纲，含文件和弹劾材料 | `litigation-legal` | `/litigation-legal:deposition-prep` |
| **简报章节起草者** | 以内部风格起草与案件理论一致的简报章节 | `litigation-legal` | `/litigation-legal:brief-section-drafter` |
| **特权日志审阅者** | 特权日志初审——显而易见的判断 + 标记供律师审阅 | `litigation-legal` | `/litigation-legal:privilege-log-review` |
| **法律保全** | 发出、更新、解除或报告法律保全令 | `litigation-legal` | `/litigation-legal:legal-hold` |
| **事项接收** | 新事项的统一接收——写入 matter.md、history.md，追加至日志 | `litigation-legal` | `/litigation-legal:matter-intake` |
| **事项简报** | 针对单一事项的深度简报——为与总法律顾问或外部律师通话做准备 | `litigation-legal` | `/litigation-legal:matter-briefing` |
| **组合状态** | 风险分布、即将到期截止日、停滞事项 | `litigation-legal` | `/litigation-legal:portfolio-status` |
| **外部律师状态** | 在活跃组合中生成每周状态请求草稿 | `litigation-legal` | `/litigation-legal:oc-status` |
| **诊所接案** | 结构化当事人接案，含跨领域问题发现和冲突标记 | `legal-clinic` | `/legal-clinic:client-intake` |
| **案例备忘录框架** | IRAC 框架式案例分析备忘录，标记研究缺口 | `legal-clinic` | `/legal-clinic:memo` |
| **研究路线图** | 待核查法规、判例法领域、Westlaw 检索词——线索，非引用 | `legal-clinic` | `/legal-clinic:research-start` |
| **诊所截止日追踪器** | 添加、报告、更新和关闭案件截止日，含防失职预警 | `legal-clinic` | `/legal-clinic:deadlines` |
| **案件状态摘要者** | 按受众呈现案件状态——当事人、教授或法庭用 | `legal-clinic` | `/legal-clinic:status` |
| **当事人信件起草者** | 日常当事人通信——预约确认、文件请求、进度更新 | `legal-clinic` | `/legal-clinic:client-letter` |
| **学生入职培训** | 学期入职——诊所程序、工具演示、实践练习 | `legal-clinic` | `/legal-clinic:ramp` |
| **学期交接** | 学期末案件交接备忘录——与入职培训对称 | `legal-clinic` | `/legal-clinic:semester-handoff` |
| **督导审阅队列** | 教授审阅队列（配置了正式审阅督导时） | `legal-clinic` | `/legal-clinic:supervisor-review-queue` |
| **司法考试辅导** | 支持管辖区的 MBE 和论文针对性练习，聚焦薄弱科目 | `law-student` | `/law-student:bar-prep-questions` |
| **苏格拉底式钻取教练** | 它发问，你作答，它反驳——绝不给出答案 | `law-student` | `/law-student:socratic-drill` |
| **IRAC 评分器** | 对您的 IRAC 论文进行结构、问题发现、规则、分析评分 | `law-student` | `/law-student:irac-practice` |
| **案例简报器** | 以您偏好的格式简述案例 | `law-student` | `/law-student:case-brief` |
| **提纲构建器** | 根据课堂笔记和教材以您的格式构建或扩展提纲 | `law-student` | `/law-student:outline-builder` |
| **冷场准备** | 预判教授提问并在课前进行演练 | `law-student` | `/law-student:cold-call-prep` |
| **考试预测者** | 分析同一教授的历年考题；预测可能的侧重点 | `law-student` | `/law-student:exam-forecast` |
| **法律写作评论者** | 对草稿进行结构性反馈——绝不代写 | `law-student` | `/law-student:legal-writing` |
| **闪卡训练师** | 生成或训练闪卡——莱特纳盒式分类 | `law-student` | `/law-student:flashcards` |
| **学习规划师** | 制定含定时学习安排的长期学习计划，并根据学习记录进行调整 | `law-student` | `/law-student:study-plan` |
| **Skill 注册表浏览器** | 搜索监控的注册表，查找社区法律 skill | `legal-builder-hub` | `/legal-builder-hub:registry-browser` |
| **Skill 安装器** | 安装社区 skill，含信任检查和 skill 质量保证 | `legal-builder-hub` | `/legal-builder-hub:skill-installer` |
| **Skill 质量保证** | 依据法律 Skill 设计框架评估 skill | `legal-builder-hub` | `/legal-builder-hub:skills-qa` |
| **社区 Skill 推荐器** | 根据其他 plugin 中的近期活动推荐社区 skill | `legal-builder-hub` | `/legal-builder-hub:related-skills-surfacer` |
| **社区 Skill 更新器** | 检查已安装社区 skill 的更新 | `legal-builder-hub` | `/legal-builder-hub:auto-updater` |
| **注册表同步** | 定期检查监控注册表中的新增和更新 skill | `legal-builder-hub` | 定时 agent |

关于 Managed Agent 部署——`agent.yaml`、叶节点子 agent、引导事件示例及每个 agent 的安全说明——请参见 **[managed-agent-cookbooks/](./managed-agent-cookbooks)**。

## 仓库结构

```
commercial-legal/         # 内部商务——供应商/NDA/SaaS 审阅、续约、升级路由
corporate-legal/          # 并购尽调、交割清单、董事会决议、主体合规
employment-legal/         # 入职/离职审阅、劳动者分类、假期、调查
privacy-legal/            # DPA、DSAR、PIA、隐私分级、政策监控
product-legal/            # 发布审阅、营销声明、"这是个问题吗？"分级
regulatory-legal/         # 监管动态监控、政策差异对比、差距追踪、NPRM 评论
ai-governance-legal/      # AI 用例分级、AIA、供应商 AI 审阅、AI 法规差距检查
ip-legal/                 # 商标清查、FTO、C&D、DMCA、开源、IP 条款、组合
litigation-legal/         # 组合、事项、保全、催告、证人准备、权利要求图表
legal-clinic/             # 诊所设置、学生入职、接案、截止日、备忘录、交接
law-student/              # 苏格拉底式训练、提纲、IRAC、司法考试准备、闪卡
legal-builder-hub/        # 含信任关卡的社区 skill 发现与安装
external_plugins/         # 合作伙伴构建的 plugin，由各供应商维护
  cocounsel-legal/        # Thomson Reuters——通过 CoCounsel Legal MCP 进行 Westlaw 深度研究
managed-agent-cookbooks/  # PrismClaw Managed Agent cookbook——每个定时 agent 一个目录
  diligence-grid/
  docket-watcher/
  launch-radar/
  reg-monitor/
  renewal-watcher/
scripts/                  # deploy-managed-agent.sh · validate.py · orchestrate.py · lint-tool-scope.py · test-cookbooks.sh
.plugin/
  marketplace.json        # plugin 注册表
```

每个 plugin 目录具有相同的结构：

```
<plugin>/
  .plugin/plugin.json
  PRISMCLAW.md               # 执业档案模板——由 /<子插件前缀>-cold-start-interview 填写
  README.md
  skills/                 # skill——引用时用 /<plugin>:<skill>
  agents/                 # 定时 agent（如有）
  commands/               # 斜杠命令——名字取自 frontmatter 的 name，需全包唯一
  hooks/                  # 前置和后置工具 hook（可选，本市场当前未配置）
```

## 快速上手

### PrismClaw Cowork

在 Cowork 中：

1. 打开 **Cowork** 标签页。
2. 点击左侧边栏中的 **自定义**。
3. 点击 **浏览 plugin** 并安装所需 plugin，**或者** 上传自定义 plugin 文件（将任意 plugin 目录压缩为 zip）。

安装后，skill 会在相关时机自动触发，斜杠命令可通过 `/` 调用，定时 agent 按其前置内容中设置的节奏运行。

### PrismClaw

```bash
# 添加市场（使用本仓库的绝对路径或 GitHub URL）
/plugin marketplace add <path-to-this-repo>

# 安装 plugin——选择与您执业领域匹配的
/plugin install commercial-legal@legal-chineselaw
/plugin install privacy-legal@legal-chineselaw
/plugin install corporate-legal@legal-chineselaw

# 重启 PrismClaw，然后为每个已安装的 plugin 运行设置。
# 这将把您的执业档案写入 ~/.prismclaw/plugins/legal-chineselaw/<plugin>/PRISMCLAW.md
/commercial-legal:cold-start-interview
/privacy-legal:cold-start-interview
/corporate-legal:cold-start-interview
```

**请先运行冷启动访谈。** plugin 中的每个其他 skill 都从它写入的执业档案中读取信息。跳过设置是 skill 输出通用结果的最常见原因。每个 plugin 的访谈需要 10-20 分钟，并会要求您指向种子文档（已签署的 MSA、playbook、先前的审阅备忘录——适合 plugin 的任何文件）。种子材料越多越好；如果您希望在 2 分钟内就开始使用，可选择**快速启动**选项，后续再细化。

**先连接研究工具。** 有了研究工具，其他一切都会更好，没有研究工具，引用将无法核实。请参阅下方 [MCP 连接器](#mcp-连接器) 中的完整列表——CourtListener、Trellis、Descrybe 和 Solve Intelligence 是引用护栏所查找的研究工具。

更新：`/plugin update`。

### PrismClaw Managed Agents

对于定时 agent——监管动态监控、续约监控、案件监控、尽调网格、发布雷达——请在您自己的调度器后端部署：

```bash
export ANTHROPIC_API_KEY=sk-ant-...
scripts/deploy-managed-agent.sh reg-monitor
scripts/deploy-managed-agent.sh renewal-watcher
scripts/deploy-managed-agent.sh docket-watcher
scripts/deploy-managed-agent.sh diligence-grid
scripts/deploy-managed-agent.sh launch-radar
```

[`managed-agent-cookbooks/`](./managed-agent-cookbooks) 下的每个模板引用与其对应 plugin 相同的系统提示和 skill。部署脚本会解析文件引用、上传 skill、创建叶节点子 agent，并将调度器 POST 至 `/v1/agents`。参见 [`scripts/orchestrate.py`](./scripts/orchestrate.py) 获取通过您自己的编排层在 agent 之间路由 `handoff_request` 事件的参考事件循环。

> **研究预览：** 子 agent 委托（`callable_agents`）是预览功能，目前支持单层委托。请参阅各 agent 的 README 了解安全层级和交接指引。

## 整体架构

| | 是什么 | 位置 |
|---|---|---|
| **Plugin** | 自包含的执业领域包——skill、agent、hook 和执业档案模板。按需安装。 | `<plugin>/` |
| **Skill** | PrismClaw 在相关时机自动调用的领域专业知识、惯例和逐步方法——以及您明确触发的斜杠动作：`/commercial-legal:review`、`/privacy-legal:dsar-response`、`/litigation-legal:claim-chart`。 | `<plugin>/skills/<skill>/SKILL.md` |
| **Agent** | 定时或事件驱动的工作流（续约监控、案件监控、监管变化监控）。在后台运行，发布至频道或写入文件。 | `<plugin>/agents/` |
| **执业档案** | 描述您的 playbook、升级规则和内部风格的纯英文 `PRISMCLAW.md`。每个 skill 读取它。 | `~/.prismclaw/plugins/legal-chineselaw/<plugin>/PRISMCLAW.md` |
| **连接器** | 将 PrismClaw 与您的数据连接的 [MCP 服务器](https://modelcontextprotocol.io/)——CLM、DMS、电子发现、研究平台、生产力工具。 | `.mcp.json`（每个 plugin） |
| **Managed agent cookbook** | `agent.yaml` + 深度 1 子 agent + 无头部署的引导示例。 | `managed-agent-cookbooks/<slug>/` |

一切都是 Markdown 和 JSON。无需构建步骤。

## 垂直 Plugin

按工作所在领域分组。每个 plugin 的冷启动访谈是将其定制到您团队的关键——从这里开始。

### 交易与顾问类

| Plugin | 新增功能 |
|---|---|
| **[commercial-legal](./commercial-legal)** | 依据 playbook 审阅供应商协议、NDA 和 SaaS 订阅。修订追踪。含取消提醒的续约登记册。升级路由。利益相关者摘要。 |
| **[corporate-legal](./corporate-legal)** | 含每格引用的表格式并购尽调。披露附表、交割清单、书面同意、董事会会议纪要。主体合规追踪器。交割后整合。 |
| **[privacy-legal](./privacy-legal)** | 隐私分级（PIA vs DPIA vs 直接推进）、PIA 生成、以控制方或处理方身份审阅 DPA、DSAR 响应。政策监控器监测政策与实践之间的偏移。 |
| **[product-legal](./product-legal)** | 依据内部风险标定审阅发布。营销声明检查。Slack 问题的"这是个问题吗？"分级。功能风险评估。 |
| **[employment-legal](./employment-legal)** | 含管辖区专属标记的入职和终止审阅。劳动者分类。假期追踪器（FMLA/CFRA/PFL/ADA）。内部调查。含州补充条款的政策起草。 |
| **[ai-governance-legal](./ai-governance-legal)** | 依据您的注册表对 AI 用例进行分级。跨适用监管框架的影响评估。供应商 AI 审阅。法规到政策差距分析。 |
| **[regulatory-legal](./regulatory-legal)** | 监管动态监控器、政策差异对比、差距追踪器、NPRM 评论期追踪器。您团队周一早间真正会读的摘要。 |
| **[ip-legal](./ip-legal)** | 商标清查、FTO 分级、C&D 起草和分级、DMCA 下架通知和反通知、开源合规、IP 条款审阅、组合追踪。 |

### 诉讼类

| Plugin | 新增功能 |
|---|---|
| **[litigation-legal](./litigation-legal)** | 服务两个层面。**内部法务/组合：** 事项接收、组合状态、法律保全、外部律师状态、催告。**律所/个人：** 时间线构建、权利要求图表（专利和民事）、证人询问准备、特权日志审阅、简报起草。 |

### 学习与实践类

| Plugin | 新增功能 |
|---|---|
| **[law-student](./law-student)** | 苏格拉底式训练、案例简报、提纲构建、IRAC 评分、冷场准备、闪卡、司法考试准备、考试预测、学习规划。**学习模式，非答题模式**——绝不代写答案。 |
| **[legal-clinic](./legal-clinic)** | 教授设置和学生学期入职。含教学立场（辅助/引导/教授）的各执业领域督导指南。含跨领域问题发现的结构化接案。含防失职谨慎度的截止日追踪。备忘录框架、当事人信件（常规 + 通俗语言）、学期交接。在 ABA 正式意见 512 框架内构建。 |

### 生态系统类

| Plugin | 新增功能 |
|---|---|
| **[legal-builder-hub](./legal-builder-hub)** | 含真实信任层的社区 skill 发现与安装——监控注册表、质量保证框架（`/legal-builder-hub:skills-qa`）、SHA 固定更新，以及任何内容进入您的环境前的强制信任检查。 |

### 外部/合作伙伴构建

[`external_plugins/`](./external_plugins) 下的 plugin 由各供应商构建和维护。它们像其他 plugin 一样从本市场安装，但供应商拥有代码、连接器和支持渠道。

| Plugin | 构建方 | 新增功能 |
|---|---|---|
| **[cocounsel-legal](./external_plugins/cocounsel-legal)** | Thomson Reuters | 含完整引用报告的 Westlaw 深度研究——每次运行涵盖最多三个美国司法管辖区的判例法、法规、监管规定、Practical Law 和二手资料。需要启用了 MCP 连接器的 CoCounsel Legal 订阅。技术支持：cocounselsupport@tr.com。 |

## 社区法律 skill 的信任层

社区正在快速构建法律 skill——LegalOps Consulting 的 `lpm-skills` 和 Lawvable 等注册表已列出了数十个。但没有人认证社区 skill，律师从 GitHub 安装随机 skill 等于安装了可访问其案件文件、执业档案和研究连接器的代码。

`legal-builder-hub` 为生态系统提供了缺失的信任层：

- **安全审查**——每次安装时进行隐藏内容扫描、注入检测和结构性信任检查
- **白名单**——默认限制性的来源关卡（注册表、发布者、连接器、许可证）
- **许可证关卡**——感知部署上下文的许可证策略（个人/律所内部/产品嵌入）
- **新鲜度关卡**——追踪捆绑参考内容（法规、法规条文、程序）是否已过验证窗口期，并在调用时发出警告
- **更新时重新扫描**——v1.0 干净但 v1.1 被污染的 skill 会被发现
- **安装日志**——可审计的记录，记录安装内容、来源、适用许可证及审查结论

白名单默认限制性。宽松模式是明确的选择。非律师用户会被路由至其律师联系人，而不是"仍然安装"按钮。

社区 skill 接受与第一方 plugin 相同的设计审查（`/legal-builder-hub:skills-qa`）。如果您为律师构建工具，在发布前请先对自己的 skill 运行质量保证。这是懂代码的律师会做的审查。

## MCP 连接器

> [!IMPORTANT]
> **先连接研究工具。** 每个 plugin 均已配置法律研究连接器——根据执业领域的不同，包括 CourtListener、Trellis、Descrybe、Solve Intelligence 等。您授权一次，此后 PrismClaw 便从权威来源获取信息，并将引用与当前数据库进行核实，而不依赖训练知识。通过研究连接器获取的引用标注了来源。仅来自模型知识的引用标记为 `[verify]`，如果根本未连接研究工具，交付物上方的审阅者说明会记录来源未经核实，以便您知晓需要自行检查。连接器使引用可信——在设置其他任何内容之前先设置好它们。

这些 plugin 附带了法律团队日常使用系统的连接器。连接器赋予 PrismClaw 从您的数据中读取（以及在范围内写入）的能力；skill 和命令使用它们。

| 连接器 | 赋予 PrismClaw 的能力 | Plugin | 备注 |
|---|---|---|---|
| **Slack** | 读取频道、搜索、发送消息和画布 | 所有 plugin | 您的工作区 |
| **Google Drive** | 读取文档、表格、幻灯片；通过链接获取 | 所有 plugin | 您的账户 |
| **CoCounsel Legal（Thomson Reuters）** | Westlaw 深度研究——含判例法、法规、监管规定、Practical Law 的引用报告 | `cocounsel-legal` | 需要客户订阅；OAuth |
| **Box** | 读取 VDR 和事项室中的文件和文件夹 | `corporate-legal` | 您的租户 |
| **Ironclad** | 读取合同登记册、续约日期、条款 | `commercial-legal` | 需要客户订阅 |
| **DocuSign / DocuSign CLM** | 信封状态、已执行合同、CLM 元数据 | `commercial-legal` | 需要客户订阅 |
| **iManage** | 从 DMS 读取——事项工作区、文档版本 | `commercial-legal`、`corporate-legal` | 需要客户订阅 |
| **Everlaw** | 电子发现生产集、标记集、时间线 | `litigation-legal` | 需要客户订阅 |
| **CourtListener** | 联邦案件和意见 | `legal-clinic`、`ip-legal`、`litigation-legal`、`law-student` | 公开；可选 API 密钥 |
| **Trellis** | 州法院案件和动议 | `litigation-legal` | 需要客户订阅 |
| **Aurora** | 诊所式事项管理和日程 | `litigation-legal` | 需要客户订阅 |
| **Definely** | 文档内起草和定义术语检查 | `commercial-legal`、`corporate-legal` | 需要客户订阅 |
| **Lawve AI** | 合同审阅辅助和条款库 | `legal-builder-hub` | 需要客户订阅 |
| **Courtroom5** | 自我代理诉讼工作流 | `legal-clinic` | 需要客户订阅 |
| **Descrybe** | 判例法研究和摘要 | `legal-clinic`、`ip-legal`、`law-student` | 需要客户订阅 |
| **Solve Intelligence** | 专利起草和审查 | `corporate-legal`、`ip-legal` | 需要客户订阅 |
| **TopCounsel** | 事项路由和外部律师团队管理 | `commercial-legal`、`corporate-legal`、`litigation-legal` | 需要客户订阅 |
| **Linear** | 发布追踪器、问题追踪 | `product-legal` | 客户工作区 |
| **Atlassian（Jira）** | 发布追踪器、问题追踪 | `product-legal` | 客户工作区 |
| **Asana** | 发布追踪器、项目追踪 | `product-legal` | 客户工作区 |

> 标注"需要客户订阅"的连接器需要客户自己的账户和 API 密钥。在每个 plugin 的 `.mcp.json` 中配置，或通过 PrismClaw 设置中的 `claude mcp` 配置。

> **构建连接器？** 请参阅 [CONNECTORS.md](./CONNECTORS.md) 了解优质法律 MCP 服务器的标准以及如何提交。

## PrismClaw for Microsoft 365

律师的工作离不开 Word 和 Excel。**本仓库中所有涉及合同的 skill 均为在 PrismClaw for Word 侧边栏中使用而编写，输出模式为修订追踪。** 包括 `commercial-legal:review`（供应商协议、NDA、SaaS 订阅）、`commercial-legal:amendment-history`、`ip-legal:ip-clause-review`、`ai-governance-legal:vendor-ai-review`、`privacy-legal:dpa-review` 以及 `corporate-legal` 中的尽调提取。审阅者像处理人工标注一样逐条接受或拒绝每项修改——编号、定义术语、交叉引用和样式均完整保留。

面向 Excel 的 skill 会生成可直接打开的工作簿：`corporate-legal:tabular-review` 生成含来源表的多工作表 `.xlsx`，`litigation-legal:claim-chart` 生成含引用列的逐要素权利要求图表，`corporate-legal:entity-compliance` 生成含截止日列的合规登记册，`commercial-legal:renewal-tracker` 导出按取消截止日排序的续约登记册。

从 **[Microsoft AppSource](https://marketplace.microsoft.com/en-us/product/office/wa200010453)** 安装 PrismClaw for Microsoft 365。安装后，您已启用的任何 plugin 中的 skill 均可通过侧边栏中的 `/` 访问，连接器也可从同一界面访问。单个对话可跨越 Word、Excel、PowerPoint 和 Outlook。

对于将加载项部署在您自己的云端（Vertex AI、Bedrock 或内部网关）而非 Anthropic API 的 IT 管理员，请参阅独立的 [`claude-for-msft-365-install`](https://github.com/anthropics/financial-services/tree/main/claude-for-msft-365-install) 工具。

## 个性化定制

这些是参考模板。当您根据团队的工作方式进行调整时，它们会变得更好——而自定义机制就是 plugin 本身，不是藏在仓库深处的配置文件。

- **运行冷启动访谈。** 它**就是**自定义机制。它询问您的执业方式，读取您的种子文档，并写入您的执业档案。每个其他 skill 都从该档案读取。带上五份已签署的 MSA、您的 playbook 和升级矩阵运行 `/commercial-legal:cold-start-interview`，将使审阅 skill 明显更精准。
- **编辑执业档案。** 您的档案位于 `~/.prismclaw/plugins/legal-chineselaw/<plugin>/PRISMCLAW.md`。直接编辑以进行小修正——错误的升级阈值、新的集成、政策更新。plugin 更新后仍然保留。
- **重新运行设置。** 当您的执业方式发生重大变化时（新管辖区、新 CLM、新政策），再次运行 `/<plugin>:cold-start-interview` 进行完整重新访谈。
- **更换连接器。** 将 `.mcp.json` 指向您的 CLM、DMS、电子发现平台、发布追踪器、HRIS。未配置连接器时 skill 会优雅降级——不会静默失败。
- **引入您的 playbook 和模板。** 将您的术语、内部风格和品牌模板放入 plugin 的 `PRISMCLAW.md` 和 `references/`。skill 会读取它们。
- **Fork skill 以适应内部风格。** 每个 skill 都是 `skills/` 下的一个 Markdown 文件。编辑步骤、关卡、输出格式。
- **添加定时 agent。** `<plugin>/agents/` 下的 agent 是带有 cron 式计划的 Markdown。为您团队需要的监控器添加自己的 agent。

无需构建步骤。一切都是 Markdown 和 JSON。

## Skill 与命令参考

所有 plugin 的完整映射。冷启动访谈是任何 plugin 中第一个要运行的内容。

### ai-governance-legal

| 命令 | Skill | 功能说明 |
|---|---|---|
| `/ai-governance-legal:cold-start-interview` | cold-start-interview | 冷启动——学习您的 AI 治理实践 |
| `/ai-governance-legal:ai-inventory` | ai-inventory | 欧盟 AI 法案逐系统清单——追踪每个系统的角色和风险等级 |
| `/ai-governance-legal:use-case-triage` | use-case-triage | 对 AI 用例进行分类——批准、有条件批准或否决 |
| `/ai-governance-legal:aia-generation` | aia-generation | 以内部格式运行 AI 影响评估 |
| `/ai-governance-legal:vendor-ai-review` | vendor-ai-review | 依据治理立场审阅供应商 AI 条款 |
| `/ai-governance-legal:reg-gap-analysis` | reg-gap-analysis | 将新 AI 法规与您的治理状态进行比对 |
| `/ai-governance-legal:policy-monitor` | policy-monitor | 保持 AI 政策与实践同步 |
| `/ai-governance-legal:policy-starter` | policy-starter | 根据已发布的模型政策起草律所 AI 使用政策，并适配到您的执业档案 |
| `/ai-governance-legal:matter-workspace` | matter-workspace | 管理事项工作区（执业层面） |

### legal-builder-hub

| 命令 | Skill | 功能说明 |
|---|---|---|
| `/legal-builder-hub:cold-start-interview` | cold-start-interview | 执业档案访谈和入门包推荐 |
| `/legal-builder-hub:registry-browser` | registry-browser | 搜索监控注册表中的社区法律 skill |
| `/legal-builder-hub:skill-installer` | skill-installer | 含信任检查的社区 skill 安装 |
| `/legal-builder-hub:skills-qa` | skills-qa | 依据设计框架评估 skill |
| `/legal-builder-hub:related-skills-surfacer` | related-skills-surfacer | 根据其他 plugin 中的活动推荐社区 skill |
| `/legal-builder-hub:auto-updater` | auto-updater | 检查已安装社区 skill 的更新 |
| `/legal-builder-hub:disable` | skill-manager | 禁用社区 skill 而不删除文件 |
| `/legal-builder-hub:uninstall` | skill-manager | 卸载通过 hub 安装的社区 skill |
| 定时 | registry-sync（agent） | 定期检查监控注册表中的更新 |

### legal-clinic

| 命令 | Skill | 功能说明 |
|---|---|---|
| `/legal-clinic:cold-start-interview` | cold-start-interview | 教授设置——执业领域、管辖区、督导风格 |
| `/legal-clinic:build-guide` | build-guide | 教授执业领域指南——接案、教学立场、审阅关卡 |
| `/legal-clinic:ramp` | ramp | 含实践练习的学生学期入职培训 |
| `/legal-clinic:client-intake` | client-intake | 含跨领域问题发现的结构化接案 |
| `/legal-clinic:client-comms-log` | client-comms-log | 记录当事人通信——每案仅追加记录 |
| `/legal-clinic:research-start` | research-start | 研究路线图——法规、判例法、检索词 |
| `/legal-clinic:memo` | memo | 含研究缺口标记的 IRAC 框架式分析备忘录 |
| `/legal-clinic:draft` | draft | 常见诊所文件的初稿 |
| `/legal-clinic:client-letter` | client-letter · plain-language-letters | 基于模板的日常当事人通信 |
| `/legal-clinic:status` | status | 按受众呈现案件状态——当事人、教授或法庭用 |
| `/legal-clinic:deadlines` | deadlines | 含防失职预警的案件截止日追踪 |
| `/legal-clinic:supervisor-review-queue` | supervisor-review-queue | 教授审阅队列（正式督导时） |
| `/legal-clinic:semester-handoff` | semester-handoff | 学期末案件交接备忘录 |

### commercial-legal

| 命令 | Skill | 功能说明 |
|---|---|---|
| `/commercial-legal:cold-start-interview` | cold-start-interview | 冷启动——学习您的商业合同实践 |
| `/commercial-legal:review` | vendor-agreement-review · nda-review · saas-msa-review | 审阅供应商协议、NDA 或 SaaS 订阅 |
| `/commercial-legal:amendment-history` | amendment-history | 追踪合同在基础版本及各修订版本间的变化 |
| `/commercial-legal:renewal-tracker` | renewal-tracker | 显示 90 天内到期取消截止日的合同 |
| `/commercial-legal:escalation-flagger` | escalation-flagger | 路由合同问题并起草请示 |
| `/commercial-legal:review-proposals` | （内部） | 审阅并批准待处理的 playbook 更新提案 |
| `/commercial-legal:matter-workspace` | matter-workspace | 管理事项工作区（执业层面） |
| — | stakeholder-summary | 将审阅内容转化为业务利益相关者摘要 |
| 定时 | renewal-watcher（agent） | 每周扫描续约登记册 |
| 定时 | deal-debrief（agent） | 每周呈现含偏差的已签协议 |
| 定时 | playbook-monitor（agent） | 当条款持续偏移时提出 playbook 更新建议 |

### corporate-legal

| 命令 | Skill | 功能说明 |
|---|---|---|
| `/corporate-legal:cold-start-interview` | cold-start-interview | 内部法务冷启动，含可选的 `--new-deal` 启动 |
| `/corporate-legal:tabular-review` | tabular-review | 表格式审阅——每文件一行，每格引用来源 |
| `/corporate-legal:diligence-issue-extraction` | diligence-issue-extraction | 按内部阈值从 VDR 文件中提取问题 |
| `/corporate-legal:material-contract-schedule` | material-contract-schedule | 构建重大合同披露附表 |
| `/corporate-legal:closing-checklist` | closing-checklist | 含关键路径的交割阻碍项清单 |
| `/corporate-legal:written-consent` | written-consent | 以内部格式起草董事会或委员会决议 |
| `/corporate-legal:entity-compliance` | entity-compliance | 跨管辖区主体合规追踪器 |
| `/corporate-legal:integration-management` | integration-management | 含同意追踪的交割后整合追踪器 |
| `/corporate-legal:matter-workspace` | matter-workspace | 管理事项工作区（执业层面） |
| — | board-minutes | 以内部格式起草董事会或委员会会议纪要 |
| — | deal-team-summary | 将尽调发现汇总为交易简报 |
| — | ai-tool-handoff | 检测 Luminance/Kira，对批量工具输出进行质量检查 |
| 定时 | dataroom-watcher（agent） | 监控 VDR 上传并发布清单状态 |

### employment-legal

| 命令 | Skill | 功能说明 |
|---|---|---|
| `/employment-legal:cold-start-interview` | cold-start-interview | 冷启动——学习管辖区和升级规则 |
| `/employment-legal:wage-hour-qa` | wage-hour-qa | 支持管辖区的工资/工时及劳动问答 |
| `/employment-legal:hiring-review` | hiring-review | 审阅 offer 信函和竞业限制条款 |
| `/employment-legal:termination-review` | termination-review | 含高风险标记检测的终止审阅 |
| `/employment-legal:worker-classification` | worker-classification | 依据州法律测试对拟议用工关系进行分类 |
| `/employment-legal:policy-drafting` | policy-drafting | 起草含州补充条款的劳动政策 |
| `/employment-legal:leave-tracker` | leave-tracker | 检查未结假期是否触发截止日提醒 |
| `/employment-legal:log-leave` | log-leave | 向假期登记册添加新假期 |
| `/employment-legal:investigation-open` | internal-investigation | 开启新的内部调查事项 |
| `/employment-legal:investigation-add` | internal-investigation | 向未结调查添加数据——文件、笔记 |
| `/employment-legal:investigation-memo` | internal-investigation | 起草或更新特权调查备忘录 |
| `/employment-legal:investigation-query` | internal-investigation | 针对未结调查日志提问 |
| `/employment-legal:investigation-summary` | internal-investigation | 从调查备忘录起草面向特定受众的摘要 |
| `/employment-legal:expansion-kickoff` | international-expansion | 启动新国家的扩张规划 |
| `/employment-legal:expansion-update` | international-expansion | 更新进行中扩张项目的状态 |
| `/employment-legal:matter-workspace` | matter-workspace | 管理事项工作区（执业层面） |
| — | handbook-updates | 比对手册变更并标记州补充条款影响 |
| 定时 | leave-tracker（agent） | 每周监控含硬截止日的未结假期 |

### ip-legal

| 命令 | Skill | 功能说明 |
|---|---|---|
| `/ip-legal:cold-start-interview` | cold-start-interview | 冷启动——学习您的 IP 实践和立场 |
| `/ip-legal:clearance` | clearance | 商标清查初步——淘汰检查 + 近似商标 |
| `/ip-legal:fto-triage` | fto-triage | 自由实施分级，非 FTO 正式意见 |
| `/ip-legal:invention-intake` | invention-intake | 发明披露初步筛查——新颖性、显而易见性、§101、禁止时限 |
| `/ip-legal:cease-desist` | cease-desist | 起草 C&D 或对收到的 C&D 进行分级 |
| `/ip-legal:takedown` | takedown | DMCA 通知、回应分级或 §512(g) 反通知 |
| `/ip-legal:infringement-triage` | infringement-triage | 跨四类 IP 权利的侵权分级 |
| `/ip-legal:ip-clause-review` | ip-clause-review | 审阅 IP 条款——转让、许可、保证 |
| `/ip-legal:oss-review` | oss-review | 开源许可证合规检查 |
| `/ip-legal:portfolio` | portfolio | 追踪 IP 组合截止日和续期 |
| `/ip-legal:matter-workspace` | matter-workspace | 管理事项工作区（执业层面） |
| 定时 | ip-renewal-watcher（agent） | 每周 IP 组合截止日报告 |

### litigation-legal

| 命令 | Skill | 功能说明 |
|---|---|---|
| `/litigation-legal:cold-start-interview` | cold-start-interview | 冷启动——风险、格局、内部简报风格 |
| `/litigation-legal:matter-intake` | matter-intake | 接收新事项——写入 matter.md 和历史记录 |
| `/litigation-legal:matter-briefing` | matter-briefing | 针对单一事项的深度简报，为通话做准备 |
| `/litigation-legal:matter-update` | matter-update | 向事项历史记录追加带日期的事件 |
| `/litigation-legal:portfolio-status` | portfolio-status | 组合汇总——风险、截止日、停滞事项 |
| `/litigation-legal:matter-close` | matter-close | 关闭事项——归档，保留记录 |
| `/litigation-legal:matter-workspace` | matter-workspace | 管理事项工作区（执业层面） |
| `/litigation-legal:demand-intake` | demand-intake | 起草前背景——当事方、事实、筹码 |
| `/litigation-legal:demand-draft` | demand-draft | 起草含 FRE 408 关卡和 .docx 输出的催告函 |
| `/litigation-legal:demand-received` | demand-received | 对收到的催告函分级——选项、组合交叉检查 |
| `/litigation-legal:subpoena-triage` | subpoena-triage | 传票分级——范围、负担、特权、方案 |
| `/litigation-legal:legal-hold` | legal-hold | 发出、更新、解除或报告法律保全令 |
| `/litigation-legal:oc-status` | oc-status | 向外部律师发送每周状态请求邮件 |
| `/litigation-legal:claim-chart` | claim-chart | 要素图表——专利或民事诉因 |
| `/litigation-legal:chronology` | chronology | 从来源和上传文件构建或更新时间线 |
| `/litigation-legal:deposition-prep` | deposition-prep | 与案件理论挂钩的证人询问提纲 |
| `/litigation-legal:privilege-log-review` | privilege-log-review | 含标记的特权日志初审 |
| `/litigation-legal:brief-section-drafter` | brief-section-drafter | 以内部风格起草简报章节 |
| 定时 | docket-watcher（agent） | 监控法院案件的申报和截止日 |

### privacy-legal

| 命令 | Skill | 功能说明 |
|---|---|---|
| `/privacy-legal:cold-start-interview` | cold-start-interview | 冷启动——学习您的隐私实践 |
| `/privacy-legal:use-case-triage` | use-case-triage | 判断是否需要 PIA、GDPR DPIA 或直接推进 |
| `/privacy-legal:pia-generation` | pia-generation | 以内部格式生成隐私影响评估 |
| `/privacy-legal:dpa-review` | dpa-review | 审阅 DPA——自动检测控制方与处理方 |
| `/privacy-legal:dsar-response` | dsar-response | 处理 DSAR 并起草回复——核实、定位、评估 |
| `/privacy-legal:reg-gap-analysis` | reg-gap-analysis | 将法规与当前政策和实践进行比对 |
| `/privacy-legal:policy-monitor` | policy-monitor | 保持隐私政策与实践同步 |
| `/privacy-legal:matter-workspace` | matter-workspace | 管理事项工作区（执业层面） |

### product-legal

| 命令 | Skill | 功能说明 |
|---|---|---|
| `/product-legal:cold-start-interview` | cold-start-interview | 冷启动——连接发布追踪器，学习风险标定 |
| `/product-legal:is-this-a-problem` | is-this-a-problem | 针对快速提问的"这是个问题吗？"快速解答 |
| `/product-legal:launch-review` | launch-review | 依据框架和风险标定进行完整发布审阅 |
| `/product-legal:marketing-claims-review` | marketing-claims-review | 审阅需要处理的营销文案声明 |
| `/product-legal:matter-workspace` | matter-workspace | 管理事项工作区（执业层面） |
| — | feature-risk-assessment | 当发布审阅标记时对单一功能进行深度风险分析 |
| 定时 | launch-watcher（agent） | 监控发布追踪器中即将到来的审阅 |

### regulatory-legal

| 命令 | Skill | 功能说明 |
|---|---|---|
| `/regulatory-legal:cold-start-interview` | cold-start-interview | 冷启动——监控列表、政策索引、重要性 |
| `/regulatory-legal:reg-feed-watcher` | reg-feed-watcher | 立即检查监管动态并报告新内容 |
| `/regulatory-legal:policy-diff` | policy-diff | 将监管变化与政策库进行比对 |
| `/regulatory-legal:gaps` | gap-surfacer | 未结差距追踪——已标记但未弥合的内容 |
| `/regulatory-legal:policy-redraft` | policy-redraft | 弥合差距的带标注政策重起草——供政策负责人审阅的提案 |
| `/regulatory-legal:comments` | （追踪器） | 审阅未结 NPRM 评论期和截止日 |
| `/regulatory-legal:matter-workspace` | matter-workspace | 管理事项工作区（执业层面） |
| 定时 | reg-change-monitor（agent） | 含重要性过滤的定时监管动态扫描 |

### law-student

| 命令 | Skill | 功能说明 |
|---|---|---|
| `/law-student:cold-start-interview` | cold-start-interview | 个人信息访谈——课程、司法考试、学习风格 |
| `/law-student:socratic-drill` | socratic-drill | 苏格拉底式训练——它发问，你作答，它反驳 |
| `/law-student:case-brief` | case-brief | 以您偏好的格式简述案例 |
| `/law-student:outline-builder` | outline-builder | 以您的格式构建或扩展提纲 |
| `/law-student:irac-practice` | irac-practice | IRAC 论文评分——结构、问题、规则、分析 |
| `/law-student:legal-writing` | legal-writing | 对您的写作进行结构性反馈——绝不代写 |
| `/law-student:cold-call-prep` | cold-call-prep | 预判教授提问并进行演练 |
| `/law-student:bar-prep-questions` | bar-prep-questions | 针对薄弱科目的 MBE 或论文题 |
| `/law-student:flashcards` | flashcards | 生成或训练闪卡——莱特纳式分类 |
| `/law-student:exam-forecast` | exam-forecast | 分析历年考题以预测可能侧重点 |
| `/law-student:study-plan` | study-plan | 构建或更新长期学习计划 |
| `/law-student:session` | study-plan | 运行专注的 N 题学习会话；更新计划 |

### cocounsel-legal（Thomson Reuters）

| 命令 | Skill | 功能说明 |
|---|---|---|
| `/cocounsel-legal:deep-research` | deep-research | 运行 Westlaw 深度研究——启动、轮询并呈现含完整引用的报告 |

## 贡献

一切都是 Markdown 和 JSON。Fork、编辑、提交 PR。

- **新 skill** → 在 `<plugin>/skills/<skill-name>/SKILL.md` 下添加，使用现有 skill 使用的前置内容（`name`、`description`、`argument-hint`）。描述保持在 1024 字符以内——这是触发信号。skill 可作为 `/<plugin>:<skill-name>` 调用。纯参考类 skill 标记 `user-invocable: false`。
- **新 agent** → 添加 `<plugin>/agents/<name>.md`，含调度前置内容和系统提示。如需无头部署，添加对应的 `managed-agent-cookbooks/<name>/`。
- **社区 skill** → 使用 `/legal-builder-hub:skill-installer` 在您的环境中测试社区 skill。hub 会在安装前对每个 skill 运行 `/legal-builder-hub:skills-qa`——依据法律 Skill 设计框架（九个设计参数、三个法律失效模式、信任面检查）对 skill 进行评分，并拒绝任何不通过的内容。
- **推送前验证 cookbook** → `bash scripts/test-cookbooks.sh` 对每个 managed-agent cookbook 进行演练并检查调度器工具范围。

## 许可证

依据 [Apache License, Version 2.0](LICENSE) 授权。

Copyright 2026 Anthropic PBC.
