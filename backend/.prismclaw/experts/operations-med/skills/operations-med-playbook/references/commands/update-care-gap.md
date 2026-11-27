---
name: update-care-gap
description: '更新筛查状态。标记筛查完成、拒绝或重置状态，基于频率自动计算下次到期日。当用户说"标记筛查"、"更新筛查状态"、"筛查完成"时使用。'
argument_hint: '[筛查记录标识或操作类型]'
category: 医疗运营
---

# /update-care-gap

## 目的

更新患者的预防性筛查记录状态，支持标记完成、拒绝或重置操作，系统将根据筛查频率自动计算下次到期日。

## 执行步骤

### 1. 明确操作类型

支持以下三种操作：

| 操作 | 说明 | 触发条件 |
|---|---|---|
| `completed` | 标记筛查已完成 | 患者已完成筛查检查 |
| `declined` | 标记筛查已拒绝 | 患者明确拒绝筛查 |
| `reset` | 重置为待筛查状态 | 需要重新安排筛查 |

### 2. 收集参数

- 必要参数：
  - `patient_id`：患者唯一标识
  - `screening_type`：筛查项目类型（如 `blood_pressure`, `cervical_cancer`, `lipid_panel`）
  - `action`：操作类型（completed / declined / reset）
- 可选参数：
  - `completion_date`：完成日期（completed 操作时必填）
  - `notes`：备注信息
  - `performed_by`：执行人

### 3. 执行状态更新

调用 `update_care_gap` 工具：

#### completed 操作流程
1. 验证筛查记录存在
2. 更新状态为 "completed"
3. 记录完成日期和执行人
4. **自动计算下次到期日**：
   - 查找该筛查类型的 USPSTF 推荐频率
   - 基于完成日期 + 频率计算下次到期日
   - 创建新的 due 记录
5. 记录操作日志

#### declined 操作流程
1. 验证筛查记录存在
2. 更新状态为 "declined"
3. 记录拒绝日期
4. 要求填写拒绝原因（可选但推荐）
5. 提示医护人员需告知患者筛查风险
6. 记录操作日志

#### reset 操作流程
1. 验证筛查记录存在
2. 将状态重置为 "due"
3. 清除之前的拒绝标记
4. 重新计算到期日（基于当前日期）
5. 记录操作日志

### 4. USPSTF 频率参考

系统内置以下筛查的频率映射：

| 筛查类型 | 频率 | 下次到期计算 |
|---|---|---|
| 血压（`blood_pressure`） | 每年 | 完成日期 + 1 年 |
| 血脂（`lipid_panel`） | 每 5 年 | 完成日期 + 5 年 |
| 糖尿病（`diabetes_screening`） | 每 3 年 | 完成日期 + 3 年 |
| 结肠癌（`colorectal_cancer`） | 每 10 年（结肠镜） | 完成日期 + 10 年 |
| 宫颈癌（`cervical_cancer`） | 每 3 年（细胞学）/ 5 年（HPV） | 完成日期 + 3/5 年 |
| 乳腺癌（`breast_cancer`） | 每年 | 完成日期 + 1 年 |
| 肺癌（`lung_cancer`） | 每年 | 完成日期 + 1 年 |
| 抑郁症（`depression_screening`） | 每年 | 完成日期 + 1 年 |
| HIV（`hiv_screening`） | 高危每年 / 低危一次性 | 完成日期 + 1 年 |
| 梅毒（`syphilis_screening`） | 高危每年 | 完成日期 + 1 年 |
| 腹主动脉瘤（`abdominal_aortic_aneurysm`） | 一次性 | 完成日期 + 10 年（标记为终身完成） |
| 骨质疏松（`osteoporosis`） | 每 2 年 | 完成日期 + 2 年 |

### 5. 结果确认

- 显示更新后的状态
- 显示计算出的下次到期日
- 确认已同步至患者筛查记录
- 提供下一步建议

## 输出格式

```
# 筛查状态更新报告

## 操作概要
- 患者 ID：[XXX]
- 姓名：[XXX]
- 筛查项目：[筛查类型名称]
- 操作类型：[completed / declined / reset]
- 操作日期：[YYYY-MM-DD]

## 更新结果

### 状态变更
- 原状态：[due / overdue / declined / completed]
- 新状态：[completed / declined / due]
- 执行人：[XXX]

### 自动计算（completed 操作）
- 完成日期：[YYYY-MM-DD]
- USPSTF 频率：[每年 / 每 N 年]
- 下次到期日：[YYYY-MM-DD]
- 已创建新的 due 记录：[是/否]

### 备注
- [notes 内容]

## 后续步骤
- [已通知患者 / 安排预约 / 待跟进]
- [下次筛查提醒已设置]
```

> 注意：本回复由 AI 辅助生成，仅提供一般性参考框架，不构成医疗建议或诊断。具体医疗问题，请咨询持有效执业证的医师。
