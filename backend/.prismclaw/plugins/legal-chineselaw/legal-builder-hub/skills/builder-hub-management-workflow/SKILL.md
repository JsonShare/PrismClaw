---
name: builder-hub-management-workflow
name_zh: 技能管理中心
description: >
  技能/插件管理中心工作流。覆盖技能市场浏览、安装/卸载/禁用、技能 QA 验证、自动更新检查、现有技能/组合总览与关联技能推荐。触发条件：'技能管理'、'安装技能'、'skill marketplace'、'技能市场'、'插件管理'、'builder hub'。
category: 技能管理
invocation_policy: user_only
priority: 10
---

<!--
v4 Phase 4 工作流技能：builder-hub-management-workflow
编排的原子技能（Phase 3 迁移为 commands / 或保留技能）：
  - registry-browser    : legal-builder-hub 下 command（技能市场 / Registry 浏览…）
  - skill-installer    : legal-builder-hub 下 command（安装新技能…）
  - skills-qa    : legal-builder-hub 下 command（技能 QA 与升级后验证…）
  - auto-updater    : legal-builder-hub 下 command（自动更新检查…）
  - disable    : legal-builder-hub 下 command（禁用/卸载技能…）
  - uninstall    : legal-builder-hub 下 command（禁用/卸载技能…）
  - skill-manager    : legal-builder-hub 下 保留技能（现有技能组合总览与管理…）
  - related-skills-surfacer    : legal-builder-hub 下 保留技能（关联技能智能推荐…）
L4 通用规范（Phase 2 内嵌）：citation + formatter + legal-safety
-->

# builder-hub-management-workflow

## 场景分流

按用户意图匹配以下场景，严格按声明顺序调用对应原子技能：

**技能市场 / Registry 浏览**
查看各 registry 可用技能、分类、版本与描述。
→ /legal-builder-hub:registry-browser

**安装新技能**
技能下载 + allowlist 校验 + freshness 检查。
→ /legal-builder-hub:skill-installer

**技能 QA 与升级后验证**
新安装或升级后的功能与回归检查。
→ /legal-builder-hub:skills-qa

**自动更新检查**
批量检查已安装技能的新版本。
→ /legal-builder-hub:auto-updater

**禁用/卸载技能**
临时禁用或彻底卸载已安装技能。
→ /legal-builder-hub:disable
→ /legal-builder-hub:uninstall

**现有技能组合总览与管理**
技能列表、分类、启用状态。
→ /legal-builder-hub:skill-manager

**关联技能智能推荐**
基于当前使用场景推荐配套技能。
→ /legal-builder-hub:related-skills-surfacer

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
