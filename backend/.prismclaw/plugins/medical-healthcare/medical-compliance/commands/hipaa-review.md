---
name: hipaa-review
description: 'HIPAA 合规审查。对照 HIPAA 隐私规则、安全规则和违规通知规则，审查医疗机构的政策、流程和技术控制，识别合规差距并提供改进建议。当用户说"HIPAA 审查"、"合规检查"、"隐私规则审查"时使用。'
argument_hint: '[审查范围或政策文件路径]'
category: 医疗合规
---

# /hipaa-review

## 目的

对照 HIPAA 规则进行系统合规审查，识别差距并提供改进建议。

## 执行步骤

### 1. 确定审查范围
- 审查对象（政策、流程、技术系统）
- 适用规则（Privacy Rule、Security Rule、Breach Notification Rule）

### 2. 审查维度

#### Privacy Rule（隐私规则）
- [ ] PHI（受保护健康信息）的使用和披露是否合规
- [ ] 患者授权（Authorization）流程是否规范
- [ ] 同意（Consent）管理是否到位
- [ ] 患者权利（访问、更正、限制）是否得到保障
- [ ] 最低必要原则是否落实

#### Security Rule（安全规则）
- [ ] 访问控制（Access Control）
- [ ] 审计控制（Audit Controls）
- [ ] 完整性（Integrity）
- [ ] 传输安全（Transmission Security）
- [ ] 身份认证（Authentication）

#### Breach Notification Rule（违规通知规则）
- [ ] 违规检测流程
- [ ] 通知时限（60 天内通知 HHS）
- [ ] 患者通知要求
- [ ] 媒体通知要求

### 3. 生成审查报告

按严重程度分级标记发现项。

## 输出格式

```
# HIPAA 合规审查报告

## 审查概要
- 审查范围：[XXX]
- 适用规则：[Privacy / Security / Breach Notification]
- 审查日期：[YYYY-MM-DD]

## 审查结果

### 🔴 严重差距
- [差距描述] — [改进建议]

### 🟠 中等差距
- [差距描述] — [改进建议]

### 🟡 轻微差距
- [差距描述] — [改进建议]

### ✅ 已合规项
- [合规项列表]

## 总体评估
[总体合规状态和优先改进建议]
```

> 注意：本回复由 AI 辅助生成，仅提供一般性参考框架，不构成医疗建议或诊断。具体医疗问题，请咨询持有效执业证的医师。
