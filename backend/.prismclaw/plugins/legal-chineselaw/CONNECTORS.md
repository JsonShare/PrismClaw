<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->

# 添加连接器

当连接到权威来源时，plugin 效果最佳。如果您构建或运营法律数据源、研究工具、CLM、DMS、电子发现平台或执业管理系统，我们希望您的 MCP 连接器加入本套件。

## 什么是好的法律 MCP 连接器

- **通过 HTTPS 的远程 MCP 服务器**，使用 OAuth 或 API 密钥身份验证（可流式 HTTP 或 SSE 传输）
- **以读取为主的工具**——搜索、获取、列出。写入工具（创建、发送、归档）需要在客户端明确确认提示；请在您的工具描述中说明。
- **结果中的来源信息**——返回来源、检索日期和可引用的标识符。plugin 按来源标记每个引用；您的连接器应使之成为可能。
- **结果中无类指令内容**——plugin 将检索到的内容视为数据，而非命令。如果您的工具结果包含元数据或系统说明，请清楚标记，使它们看起来不像嵌入指令。
- **优雅降级的速率限制和错误**——当连接器无响应时 plugin 有备用方案；干净的错误比超时更好。

## 如何提交

1. 发布您的 MCP 服务器并记录其工具、身份验证流程和数据覆盖范围。
2. 打开 PR 把您的服务器添加到相关 plugin 的 `.mcp.json` 中，包含 URL、身份验证方法和关于它给 PrismClaw 提供什么的单行描述。
3. 包含关于它对哪些执业领域 / plugin 最有用的说明。
4. 我们会针对 plugin 工作流进行测试并合并。通过检索质量和注入抗性检查的连接器会进入默认 `.mcp.json`；其他会记录在 plugin README 中供用户自行添加。

## 当前连接器

随每个 plugin 默认 `.mcp.json` 附带的连接器：

| 连接器 | Plugin |
|---|---|
| **Slack** | 全部 12 个 |
| **Google Drive** (`gdrive`) | 全部 12 个 |
| **CourtListener** | legal-clinic, ip-legal, litigation-legal, law-student |
| **Descrybe** | legal-clinic, ip-legal, law-student |
| **Definely** | commercial-legal, corporate-legal |
| **iManage** | commercial-legal, corporate-legal |
| **Solve Intelligence** | corporate-legal, ip-legal |
| **TopCounsel** | commercial-legal, corporate-legal, litigation-legal |
| **Box** | corporate-legal |
| **Ironclad** | commercial-legal |
| **DocuSign / DocuSign CLM** | commercial-legal |
| **Everlaw** | litigation-legal |
| **Trellis** | litigation-legal |
| **Aurora** | litigation-legal |
| **Courtroom5** | legal-clinic |
| **Lawve AI** | legal-builder-hub |
| **Linear** | product-legal |
| **Atlassian (Jira)** | product-legal |
| **Asana** | product-legal |

请参阅每个 plugin 目录中的 `.mcp.json` 获取权威列表。

## 待添加连接器

这些会使特定 plugin 显著更有用。如果您构建或运营其中一个，请参阅上方"如何提交"。

- **IP 管理系统**（Anaqua、Clarivate IPfolio、AppColl、Patrix、Alt Legal、FoundationIP）——用于 `ip-legal` 组合追踪的完整事项同步
- **按客户编号的 USPTO**——完整组合状态和截止日，而不仅仅是单个申请查询
- **USPTO TSDR / 商标状态**——用于 `ip-legal` 品牌管理的商标状态和截止日
- **用于 OSS 请求的 Jira / Linear / Asana**——`ip-legal` OSS 清查可监控并响应收到的工单
- **Thomson Reuters**（CoCounsel、Practical Law、Westlaw）——每个 plugin 的研究和起草
- **SS&C Intralinks / Datasite**——`corporate-legal` 尽调的 VDR 访问
- **超越读取的 Relativity / Everlaw**——`litigation-legal` 的电子发现工作流
- **州律师协会 CLE 追踪器**——`law-student` 司法考试准备
- **法院电子归档系统**（PACER 写入、州电子归档）——当然带有硬不可逆关卡
- **全球 AI 监管追踪器**（techieray.com/GlobalAIRegulationTracker）——带结构化 API 的管辖区标记 AI 监管追踪。策划、核实、多管辖区。将成为 `ai-governance-legal` 和 `regulatory-legal` 的主要来源相邻提要。
- **监管主要来源**——连接到官方登记簿（eCFR、Federal Register、EUR-Lex、legislation.gov.uk、Australian Federal Register of Legislation、Singapore Statutes Online），绕过许多立法网站使用的 agent 拦截器。一个策划的监管知识库将是高价值的补充。

## 问题

在本仓库中打开 issue。关于合作或集成的问题，请参阅每个 plugin README 上的联系方式。
