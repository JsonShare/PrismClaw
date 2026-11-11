---
name: compliance-customize
description: '引导式自定义合规执业档案——只改一项，无需重跑整个冷启动访谈。'
argument_hint: '[章节名称，或描述你想修改的内容]'
category: 医疗合规
customizes: medical-compliance
---

# 自定义医疗合规配置

## 读取当前配置

读取 `~/.prismclaw/plugins/medical-healthcare/medical-compliance/PRISMCLAW.md`，获取当前自定义配置。

## 显示可自定义项

列出所有可自定义项及其当前值：

- **机构信息** — 名称、类型、合规负责人
- **活跃模块** — HIPAA、数据隐私、审计追踪
- **适用法规** — HIPAA、个保法等
- **集成状态** — EMR 日志、监测系统等
- **风险阈值** — 合规风险分级标准

## 修改配置

询问用户想修改什么，然后修改对应配置项。修改后保存。

## 关闭

确认修改完成，告知用户如何恢复默认。

## 护栏

- 永远不要删除章节
- 一次只改一项
- 标记内部矛盾

> 注意：本回复由 AI 辅助生成，仅提供一般性参考框架，不构成医疗建议或诊断。具体医疗问题，请咨询持有效执业证的医师。
