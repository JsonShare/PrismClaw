---
name: log-leave
description: '向休假登记册添加新休假条目，包含跟踪期限所需的最少信息。当员工开始休假且你希望跟踪器从第一天起监控审批、证明和耗尽时钟时使用。

  '
argument_hint: '[描述休假——员工/角色、类型、省市、开始日期]'
category: 劳动用工
---

<!--
This file is a Chinese translation and adaptation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
改造为中国劳动法语境。
-->


# /log-leave

向 `~/.prismclaw/assets/plugins/legal-chineselaw/employment-legal/leave-register.yaml` 添加新休假条目，包含开始跟踪期限所需的最少信息。当员工开始休假且你希望跟踪器从第一天起监控时钟时使用。

## 说明

1. 读取 `~/.prismclaw/assets/plugins/legal-chineselaw/employment-legal/PRISMCLAW.md` → 省市表和系统部分。

2. 在单个提示中询问以下所有内容——不要逐个询问：

   > 几个快速问题以设置休假跟踪：
   >
   > - 员工姓名或角色（匿名化即可）
   > - 他们在哪里工作？（省市——这决定适用哪些规则）
   > - 休假类型：
   >   - 带薪年休假（年休假条例）
   >   - 病假/医疗期（须明确工龄，影响医疗期长短）
   >   - 产假（98天基础+各省市生育奖励假，须明确是否生育、顺产/剖宫产等）
   >   - 陪产假（各省市不同，通常7-30天）
   >   - 工伤停工留薪期（工伤保险条例第33条）
   >   - 婚假（3天+各地婚育假）
   >   - 丧假（3天）
   >   - 其他（请说明）
   > - 休假开始日期
   > - 是否为间歇性休假？
   > - 预期返回日期（如果已知——不知道则留空）
   > - 休假审批文件是否已归档？如果是，何时？
   > - 是否需要医疗证明？如果是，是否已提交？

3. 使用 `~/.prismclaw/assets/plugins/legal-chineselaw/employment-legal/PRISMCLAW.md` 中的省市表，查找此休假类型在此省市的适用休假权益。对于医疗期，需要员工在本单位工龄（影响医疗期时长）：
   - 工龄不满10年：在本单位工作年限不满5年的，医疗期3个月；5年以上的，6个月
   - 工龄10年以上：在本单位工作年限不满5年，医疗期6个月；5-10年，9个月；10-15年，12个月；15-20年，18个月；20年以上，24个月

4. 根据提供的信息计算第一个即将到来的截止日期：
   - 休假审批文件尚未归档 → 提示及时审批记录
   - 医疗证明已请求但未收到 → 提示催要证明
   - 医疗期到期尚未确认返岗安排 → 提示需作出决策

5. 使用休假跟踪器代理的休假登记册格式向 `~/.prismclaw/assets/plugins/legal-chineselaw/employment-legal/leave-register.yaml` 写入新条目。如果文件不存在，创建它。

6. 用一行确认：
   > "已记录。[员工/角色] — [休假类型] — [省市] — [日期] 开始。第一个需关注事项：[是什么及何时]。休假跟踪器将自动提醒。"

## 示例

```
/employment-legal:log-leave
```

```
/employment-legal:log-leave
张某（高级工程师，在上海工作）今天因病开始病假。预计3周。已提供病假条。
```

```
/employment-legal:log-leave
李某（广州研发中心，工龄8年）因病住院，预计需要较长时间治疗。请记录医疗期。
```


> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
