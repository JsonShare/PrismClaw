---
name: ip-patent-workflow
name_zh: 专利工作流
description: >
  专利领域工作流。覆盖 FTO 自由实施、侵权分流、发明交底书收案、权利要求结构检查。触发条件：'专利'、'FTO'、'侵权'、'infringement'、'发明交底'、'invention disclosure'、'权利要求'、'claim draft'。
category: 知识产权
invocation_policy: user_only
priority: 10
---

# ip-patent-workflow

## 场景分流

按用户意图匹配以下场景，严格按声明顺序调用对应原子技能：

**FTO 自由实施分析**
产品入市前做 Freedom to Operate 检索与风险分级。
→ `skills/ip-law-playbook/references/commands/fto-triage.md`

**侵权初步评估 / 分流**
接到侵权指控或发现疑似侵权，做 triage。
→ `skills/ip-law-playbook/references/commands/infringement-triage.md`

**发明交底书收案**
从研发收 invention disclosure，做格式整理和可专利性初筛。
→ `skills/ip-law-playbook/references/commands/invention-intake.md`

若场景模糊，先向用户澄清匹配哪一分支，不要盲跳步骤。

---

## 权利要求结构检查（scripts/audit_claims.py）

起草或审查权利要求书时，用这个脚本做确定性结构检查，不要靠肉眼逐条核。输入是纯 UTF-8 文本（权利要求书正文），输出分级问题清单。

```bash
python scripts/audit_claims.py <claims.txt>
python scripts/audit_claims.py <claims.txt> --json
```

检查项：

| 代码 | 级别 | 含义 |
|---|---|---|
| `NO_CLAIMS` | ERROR | 未识别到以「1.」「1、」起始的权利要求 |
| `NUMBER_SEQUENCE` | ERROR | 编号不连续 |
| `EMPTY` | ERROR | 某条权利要求正文为空 |
| `PLACEHOLDER` | ERROR | 仍含 `[待确认]` / `[TO CONFIRM]` 标记 |
| `INDEPENDENT_REFERENCE` | ERROR | 权利要求 1 引用了其他权利要求 |
| `FORWARD_REFERENCE` | ERROR | 引用了编号不小于自身的权利要求 |
| `MISSING_REFERENCE` | ERROR | 引用的权利要求不存在 |
| `NO_REFERENCE` | WARNING | 编号 >1 但无引用，需确认是否为独立权利要求 |
| `TRANSITION` | WARNING | 缺「其特征在于」过渡语 |
| `TOO_SHORT` | WARNING | 正文少于 25 字，确认是否完整限定 |
| `RESULT_LANGUAGE` | WARNING | 含「效果更好」「显著提高」等结果性/宣传性措辞 |
| `ANTECEDENT_BASIS` | WARNING | 「所述 X」的 X 可能缺前置基础 |

使用规则：

- 脚本只做结构检查，**不判断新颖性、创造性、支持性**，这些仍由你按专利法与人工作业完成。
- 脚本报 ERROR 时必须先修正再继续；报 WARNING 时逐条判断是真实缺陷还是误报，并在结论中说清哪些采纳、哪些不采纳及理由。
- 脚本输出是检查结果，不是法律意见；不得把脚本 PASS 当作可专利性结论。
- 退出码：存在 ERROR 时为 1，仅 WARNING 或通过时为 0。

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
