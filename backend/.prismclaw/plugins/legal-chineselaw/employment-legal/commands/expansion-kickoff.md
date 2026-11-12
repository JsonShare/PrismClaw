---
name: expansion-kickoff
description: '为新省市或国家启动劳动法律合规规划——收集接收信息、运行直签劳动合同 vs. 劳务派遣 vs. 外包框架、起草跨职能合规问题、提取省市特定标志、创建持久追踪器。当有人说"我们要在[省市/国家]招人"、"扩张到[地区]"或"在[省市]的第一位员工"时使用。

  '
argument_hint: '[省市或国家名称]'
category: 劳动用工
---

<!--
This file is a Chinese translation and adaptation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
改造为中国劳动法语境。
-->


# /expansion-kickoff

为新省市或国家启动劳动法律合规项目——收集接收信息、运行用工形式框架分析、起草跨职能合规问题、提取省市/国家特定标志、创建持久追踪器。

## 说明

1. 加载 `~/.prismclaw/assets/plugins/legal-chineselaw/employment-legal/PRISMCLAW.md` → 省市足迹、升级表。
2. 加载 `international-expansion` 参考 skill 并运行完整工作流。
3. 如果此省市/国家的追踪器文件已存在（`~/.prismclaw/assets/plugins/legal-chineselaw/employment-legal/expansion-[slug].yaml`），
   标记它："[省市/国家] 的扩张追踪器已存在。使用
   `/employment-legal:expansion-update [省市/国家]` 来更新它，或确认你想重新开始。"
4. 完成后创建 `~/.prismclaw/assets/plugins/legal-chineselaw/employment-legal/expansion-[slug].yaml`。

## 中国境内省市扩张关注点

**劳动法律合规核心问题（新省市开展业务前必须评估）：**

1. **劳动合同模板本地化** — 各省市劳动合同示范文本不同；需确认本省市最新版本及必要条款要求。

2. **最低工资标准** — 新省市的最低工资标准（须确认当前有效标准）；试用期工资下限。

3. **社会保险和住房公积金** — 各省市社保公积金缴纳比例不同；新员工须在用工之日起30天内完成社保登记。

4. **工时制度审批** — 如需在新省市实行综合计算工时制或不定时工时制，须向当地劳动行政部门申请审批。

5. **各地高院劳动争议裁判口径** — 了解新省市的劳动仲裁机构处理特点和高院指导意见。

6. **竞业限制补偿金标准** — 各省市对竞业限制补偿金下限规定不同。

7. **女职工保护** — 产假天数（含生育奖励假）按各省市规定执行。

8. **外籍/港澳台员工** — 如涉及外籍员工，需确认工作许可要求及社保缴纳安排。

## 示例

```
/employment-legal:expansion-kickoff 浙江省
```

```
/employment-legal:expansion-kickoff 新加坡
```

```
/employment-legal:expansion-kickoff
(skill 将询问哪个省市或国家)
```

> 详细的用工形式框架、跨职能合规问题、简报模板和追踪器架构位于 `international-expansion` 参考 skill 中——在进行实质性工作之前加载它。


> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
