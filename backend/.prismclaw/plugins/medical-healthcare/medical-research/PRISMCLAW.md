<!--
配置位置：~/.prismclaw/plugins/medical-healthcare/medical-research/PRISMCLAW.md
-->

# 医学研究执业档案
*由冷启动在 [DATE] 编写。活跃模块：[文献综述 | 研究设计 | 统计分析]*
*如果是 `[PLACEHOLDER]`，运行 `/research-cold-start-interview`。*

---

## 机构档案

**机构名称：** [PLACEHOLDER]
**机构类型：** [PLACEHOLDER — 学术机构 / 研究医院 / 企业研发 / 独立研究者]
**研究领域：** [PLACEHOLDER]
**主要研究者：** [PLACEHOLDER]
**伦理审查委员会（IRB）：** [PLACEHOLDER — 有 / 无 / 外部]
**适用法规：** [PLACEHOLDER — GCP / 赫尔辛基宣言 / 机构政策]

---

## 谁在使用这个

**角色：** [PLACEHOLDER —  principal investigator / co-investigator / research fellow / biostatistician / student]
**升级联系人：** [PLACEHOLDER]

---

## 可用集成

| 集成 | 状态 | 不可用时的备用方案 |
|---|---|---|
| PubMed/文献数据库 | [✓ / ✗] | 手动检索 |
| 统计软件（R/Python） | [✓ / ✗] | 手动计算 |
| 临床试验注册库 | [✓ / ✗] | 手动查询 |
| 参考管理工具 | [✓ / ✗] | 手动管理引用 |

---

## 输出规范

**工作产品标题：**
- 研究者：`研究草稿 — 须经同行评审`
- 学生/受训人员：`学习材料 — 非最终结论`

**研究声明。** 本 plugin 的输出用于研究辅助，研究结论须经适当的同行评审和统计验证。

---

## 研究方法论

### 一、研究类型

| 类型 | 描述 | 证据等级 |
|---|---|---|
| 系统评价/Meta分析 | 综合多项研究 | 1a |
| 随机对照试验（RCT） | 金标准研究设计 | 1b |
| 队列研究 | 观察性研究 | 2a |
| 病例对照研究 | 回顾性研究 | 2b |
| 横断面研究 | 患病率调查 | 3 |
| 病例报告 | 单个病例 | 4 |
| 专家意见 | 共识观点 | 5 |

### 二、文献检索策略

1. **数据库选择** — PubMed、Embase、Cochrane、Web of Science
2. **检索式构建** — MeSH 词 + 自由词 + 布尔运算符
3. **纳入/排除标准** — 预设的 PICO 框架
4. **时间范围** — 根据研究问题确定
5. **语言限制** — 通常限定英文

### 三、统计方法选择

根据研究类型和数据类型选择：
- **连续变量** — t 检验、ANOVA、回归
- **分类变量** — 卡方检验、Fisher 精确检验
- **生存数据** — Cox 比例风险模型、Log-rank 检验
- **Meta 分析** — 固定效应或随机效应模型

---

## 共享护栏

**没有静默补充。**

**来源标记：**
- `[PubMed]` — 文献来自 PubMed
- `[user provided]` — 用户提供的信息
- `[模型知识—待核验]` — 其他所有内容

---

*重新运行完整访谈：`/research-cold-start-interview --redo`*