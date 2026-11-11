---
name: hipaa-compliance-workflow
name_zh: HIPAA 合规工作流
description: >
  HIPAA 合规工作流入口。覆盖隐私规则审查、安全规则审查、违规通知规则审查及合规差距分析。触发条件：'HIPAA'、'合规审查'、'隐私规则'、'安全规则'。
category: 医疗合规
invocation_policy: auto
priority: 10
---

# hipaa-compliance-workflow

## 场景分流

**全面 HIPAA 审查**
对隐私、安全和违规通知规则进行全面审查。
→ /medical-compliance:hipaa-review

**特定规则审查**
针对单一规则（Privacy Rule 或 Security Rule）的审查。
→ /medical-compliance:hipaa-review

**合规差距分析**
对比当前实践与 HIPAA 要求，识别差距。
→ /medical-compliance:hipaa-review

**政策合规检查**
审查特定政策文件的 HIPAA 合规性。
→ /medical-compliance:hipaa-review

---

## 审查框架

### Privacy Rule 核心要求
1. PHI 使用和披露的合规性
2. 患者授权流程
3. 最小必要原则
4. 患者权利保障
5. 业务关联协议（BAA）

### Security Rule 核心要求
1. 访问控制
2. 审计控制
3. 数据完整性
4. 传输安全
5. 身份认证

### Breach Notification Rule
1. 违规检测和评估
2. 通知时限（60天）
3. 患者通知
4. HHS 通知
5. 媒体通知

---

## 输出规范

所有发现按严重程度分级，附改进建议和优先级排序。

---

## 医疗安全声明

本回复由 AI 辅助生成，仅提供一般性参考框架，不构成医疗建议、诊断或治疗方案，也不替代具有执业资质医师的专业判断。具体医疗问题，请咨询持有效执业证的医师或获准从事医疗业务的机构。

涉及临床决策、处方开具、诊断编码、医保申报等需由专业人员确认的正式输出，须经授权医师/编码员独立审查、修改并以医疗机构名义出具后方可使用。

本回复中的医学知识、编码规则或监管口径为参考时点的有效状态，后续修订或地方实施细则差异请以最新正式发布版本为准。
