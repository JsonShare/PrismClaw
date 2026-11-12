---
name: ip-customize
description: '引导式自定义您的 IP 执业档案——更改一件事而不 重新运行整个冷启动访谈。调整风险姿态、升级 联系人、投资组合范围、品牌保护策略、执行姿态、
  清除阈值、OSS 审查规则或事项工作区路径。当 用户说"更改我的 [某事]"、"更新我的档案"、"编辑我的配置"、 或"自定义"时使用。

  '
argument_hint: '[section name, or describe what you want to change]'
category: 知识产权
customizes: ip-legal
---

<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->

# 自定义知识产权配置

## 读取当前配置

读取 `~/.prismclaw/assets/plugins/legal-chineselaw/ip-legal/PRISMCLAW.md`（以及上一层的 `~/.prismclaw/assets/plugins/legal-chineselaw/company-profile.md`），获取当前自定义配置。如果插件配置不存在或仍包含 `[PLACEHOLDER]` 值，提示用户先运行冷启动访谈。

## 显示可自定义项

列出所有可自定义项及其当前值：

- **公司/你是谁** — 名称、行业、司法管辖区、阶段、执业设置 *(在所有 12 个插件之间共享——更改通过 `company-profile.md` 流动)*
- **IP 执业档案** — 范围内的 IP 类型（专利、商标、版权、商业秘密、外观设计），执业方向（起诉 / 交易 / 执法 / 内部投资组合）
- **风险姿态** — 保守 / 中等 / 激进，每个对许可阈值、FTO 意见和停止侵权升级意味着什么
- **人员** —— IP 法律顾问、按 IP 类型划分的外部律师事务所、执法升级链、发明委员会
- **投资组合** —— 专利族、商标类别、关键商标、注册国家、监控服务
- **品牌保护** —— 对市场下架、域名抢注、戏仿/合理使用呼叫的执法姿态
- **执法姿态** —— 何时发送 C&D 与治愈信与诉讼；按侵权类型升级触发器
- **许可和 FTO** —— 搜索供应商、许可信心阈值、FTO 意见格式
- **OSS 审查** —— 许可层策略、阻止许可的许可证、新依赖项的审查节奏
- **工作流** —— 事项工作空间（事项 ID、族 ID）、案卷订阅源、发明接收表
- **集成** —— 专利案卷系统 / 商标局连接器 / Slack / 文档存储状态、备用方案

## 修改配置

询问用户想修改什么，然后修改对应配置项。展示当前值，询问新值，说明下游影响，确认后写入配置。

示例：
- *添加新的商标监控类别：* "`/portfolio` 将在监控报告中包括 XX 类别，`/infringement-triage` 将相应路由 XX 类别发现。"
- *执法姿态激进 → 中等：* "`/cease-desist` 将为模糊情况提供治愈信草稿作为第一选项，而不是直接发送 C&D。"
- *新的阻止许可 OSS 许可证：* "`/oss-review` 将使包含此许可证的审查失败，而不是警告。"

对于共享档案更改（公司名称、行业、司法管辖区、执业设置、阶段）：写入 `~/.prismclaw/assets/plugins/legal-chineselaw/company-profile.md` 并说明影响范围。修改后保存。

## 关闭

确认修改完成，告知用户如何恢复默认。

## 护栏

- **永不删除部分。** 如果用户想要从范围中"删除"某种 IP 类型，将其设置为 `[Not currently in scope]` 并解释会删除什么。
- **标记内部不一致。** 如果更改会使档案不一致（例如，商标不在范围 + 配置了商标监控服务；或激进执法姿态 + "所有 C&D 发给外部法律顾问"），标记紧张关系。
- **标记护栏降级。** `[review]` 标志、来源归因标记和引用权威上的 `[verify]` 标记是承重的——不要移除。许可信心在 `/clearance` 输出中是承重的——不要压制。
- **一次更改一个。** 不要重新询问整个访谈。

> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
