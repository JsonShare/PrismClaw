---
name: law-student-practice-workflow
name_zh: 法学生学习工作流
description: >
  法学生学习方法工作流。从学习计划到判例摘要、IRAC 训练、法律写作、苏格拉底模拟、闪卡、大纲、课堂 cold call、模拟题、考前押题完整闭环。触发条件：'学法律'、'学习计划'、'case brief'、'IRAC'、'法学写作'、'法考'、'期末考试'。
  不适用：真实案件的法律意见与文书定稿（见 legal-clinic 或执业律师），以及代写作业与考试答案。
category: 法学生工具
invocation_policy: user_only
priority: 10
---



# law-student-practice-workflow

## 场景分流

按用户意图匹配以下场景，严格按声明顺序调用对应原子技能：

**制定学期/备考学习计划**
根据课程、法考、期末时间做分层学习计划。
→ `skills/law-trainee-playbook/references/commands/study-plan.md`

**判例/案例摘要 Case Brief**
IROAC 拆判决（Issue/Rules/Opinion/Analysis/Conclusion）。
→ `skills/law-trainee-playbook/references/commands/case-brief.md`

**IRAC 法律论证训练**
按 Issue-Rule-Application-Conclusion 四步法写论证。
→ `skills/law-trainee-playbook/references/commands/irac-practice.md`

**法律文书与学术写作**
memo、brief、essay、dissertation 写作指导。
→ `skills/law-trainee-playbook/references/commands/legal-writing.md`

**苏格拉底式提问/课堂模拟**
用 Socratic Drill 模拟课堂提问场景与压力测试。
→ `skills/law-trainee-playbook/references/commands/socratic-drill.md`

**知识点闪卡**
高频考点、法条、学说做 flashcards 间隔重复。
→ `skills/law-trainee-playbook/references/commands/flashcards.md`

**课程大纲构建 Outline**
整门课大纲式梳理，关联案例、法条、学说。
→ `skills/law-trainee-playbook/references/commands/outline-builder.md`

**Cold Call 课堂点答准备**
针对指定案例和问题准备应答框架。
→ `skills/law-trainee-playbook/references/commands/cold-call-prep.md`

**法考/期末模拟题**
生成客观题/主观题练习题并附带答案与解析。
→ `skills/law-trainee-playbook/references/commands/bar-prep-questions.md`
中国法考按客观题、主观题两阶段出题；MBE/UBE/NextGen 题型只适用于美国 bar，不得与法考混用。题目所依法条版本与真题来源须标 [待核验]。

**考前押题与重点预测**
基于规律预测期末/法考重点范围。
→ `skills/law-trainee-playbook/references/commands/exam-forecast.md`

若场景模糊，先向用户澄清匹配哪一分支，不要盲跳步骤。

---

## 输出规范

### 来源标注（citation）
所有结论附来源条目编号。引用合同、法规、通信原文时，格式为：
`[序号] 文件名:行号 | 原文片段或条款编号`

示例：
- `[1] 供货合同.pdf:第4.2条 | 买方应于收到发票后30日内付款`
- `[2] 民法典.pdf:第577条 | 当事人一方不履行合同义务或者履行合同义务不符合约定的，应当承担继续履行、采取补救措施或者赔偿损失等违约责任`
- `[3] 2024-03-15对方邮件.eml:L12-15 | "我方将于本月底安排付款"`

引用判例时标注案号：`[4] (2023)京01民终12345号判决书:L45-50`

### 输出格式（formatter）
所有最终输出严格分四段，每段独立成章：

1. **结论** — 你判断的事项核心观点、建议采取的下一步动作（一句话 + 详细展开）
2. **依据** — 支撑结论的合同条款、法律规定、证据要点（逐条列出，附来源标注）
3. **风险** — 不按建议行动的后果、对方可能的抗辩、时效/程序风险（逐条列出，附触发条件和概率）
4. **来源** — 本回复所依据的全部材料清单，对应依据段落的 `[序号]` 引用

草稿阶段（未最终确认前）允许插入 `[PLACEHOLDER——待核实:____]` 占位符，但必须标明确切待核实点与负责角色。

---

## 免责声明

**法律安全声明（legal-safety）**

本回复由 AI 辅助生成，仅提供一般性参考框架，不构成法律意见，也不替代具有执业资质律师的专业判断。具体法律问题，请咨询持有效执业证的律师或获准从事法律业务的机构。
中国法律服务市场监管：未经司法行政机关许可，任何 AI 系统不得出具法律意见书或从事有偿诉讼代理。涉及起诉状、答辩状、法律意见书等需由律师签名的正式文书，须经委托律师独立审查、修改并以律师事务所名义出具后方可使用。
本回复中的法规条款、裁判观点或监管口径为参考时点的有效状态，后续修订或地方实施细则差异请以最新正式发布版本为准。
