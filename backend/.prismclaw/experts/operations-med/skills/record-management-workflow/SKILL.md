---
name: record-management-workflow
name_zh: 病历管理工作流
description: >
  病历管理工作流入口。覆盖病历归档、调取、质量检查与保存期限管理。触发条件：'病历管理'、'病历归档'、'病历质量'、'调取病历'。不适用于医生搜索、预约时段与预防性筛查差距（属门诊运营，入口见 `insurance-claims-workflow`）。
category: 医疗运营
invocation_policy: user_only
---

# record-management-workflow

## 场景分流

**病历归档**
检查病历完整性并归档。
→ `skills/operations-med-playbook/references/commands/record-management.md`

**病历调取**
处理病历调取请求。
→ `skills/operations-med-playbook/references/commands/record-management.md`

**质量检查**
批量检查病历质量。
→ `skills/operations-med-playbook/references/commands/record-management.md`

**保存期限管理**
管理病历保存和销毁。
→ `skills/operations-med-playbook/references/commands/record-management.md`

**医生搜索 / 预约时段 / 预防性筛查差距 / 筛查状态更新**
不属于病案管理，属门诊运营口径；入口与判定标准见 `insurance-claims-workflow`，本技能不重复定义。

---

## 管理框架

### 归档标准
1. 病历完整性检查
2. 签名和授权验证
3. 分类和编码
4. 归档位置记录

### 保存要求
- 保存年限按属地法规与本院制度当期版本确定，输出前核对，不凭记忆填写
- 住院病历：[待核验]
- 门诊病历：[待核验]
- 特殊病历（如涉及纠纷、传染病、职业病）：长期保存，具体范围按属地规定 [待核验]

### 调取流程
1. 权限验证
2. 调取申请
3. 病历定位
4. 交付记录

---

## 医疗安全声明

本回复由 AI 辅助生成，仅提供一般性参考框架，不构成医疗建议、诊断或治疗方案，也不替代具有执业资质医师的专业判断。具体医疗问题，请咨询持有效执业证的医师或获准从事医疗业务的机构。

涉及临床决策、处方开具、诊断编码、医保申报等需由专业人员确认的正式输出，须经授权医师/编码员独立审查、修改并以医疗机构名义出具后方可使用。

本回复中的医学知识、编码规则或监管口径为参考时点的有效状态，后续修订或地方实施细则差异请以最新正式发布版本为准。
