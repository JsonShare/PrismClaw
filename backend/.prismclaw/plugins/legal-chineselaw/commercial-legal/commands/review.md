---
name: review
description: '根据您的剧本审查供应商协议、保密协议或 SaaS/软件订阅合同。 从标题中识别协议结构，路由到正确的审查 skill （vendor-agreement-review、nda-review、saas-msa-review），并将输出
  集成到单个备忘录中。当用户说"审查这份合同"、"检查这份MSA/框架协议"、 "这份保密协议可以签吗"、"看看这份SaaS协议"或附加入站协议进行审查时使用。

  '
argument_hint: '[文件路径 | 企业协作文档链接 | [合同管理系统ID] | 粘贴文本]'
category: 商事合同
---

<!--
本文件为 Anthropic PBC 原版的中国法律实践适配版。
原版：https://github.com/anthropics/legal-chineselaw
授权协议：Apache License 2.0
-->


# /review

根据 `~/.prismclaw/plugins/legal-chineselaw/commercial-legal/PRISMCLAW.md` 中的剧本审查入站协议。从标题中识别协议结构，选择适当的 skill，并且——如果启用了 confirm_routing——在继续之前与用户确认。

## 说明

1. **加载 `~/.prismclaw/plugins/legal-chineselaw/commercial-legal/PRISMCLAW.md`。** 如果存在占位符，停止并提示："运行 `/commercial-legal:cold-start-interview` 先配置执业档案——我需要了解你的剧本才能根据它审查合同。"

   同时读取 `~/.prismclaw/plugins/legal-chineselaw/commercial-legal/PRISMCLAW.md` → `## 审查偏好` → `confirm_routing`。如果字段缺失，将其视为 `true`。

2. **获取协议：** 从文件路径、企业协作文档链接、[合同管理系统 ID] 或粘贴的文本。如果未提供，请询问。

3. **阅读文档结构——首先是标题。**

   在阅读正文之前，提取：
   - 主协议标题（例如，"主服务协议"、"保密协议"、"软件订阅服务合同"）
   - 所有附件、附表、补充协议和附录的标题（例如，"附件A — 个人信息处理协议"、"附表一 — 服务等级协议(SLA)"、"补充协议 — 数据安全要求"）

   这是路由信号。不要仅依赖正文关键词——带有"保密条款"的 40 页主服务协议不是保密协议。

4. **根据文档结构选择 skill。**

   将每个识别的文档或部分映射到 skill：

   | 文档 / 部分标题包含 | Skill |
   |---|---|
   | 保密协议、NDA、保密合同、信息保密协议（作为*主*协议） | **nda-review** |
   | 主服务协议、框架协议、服务合同、技术服务合同、咨询协议、采购合同 | **vendor-agreement-review** |
   | SaaS 协议、软件订阅合同、云服务合同、带自动续期的订单确认书、含经常性费用的软件许可合同 | **saas-msa-review**（vendor-agreement-review 的覆盖层） |
   | 个人信息处理协议、数据处理协议、DPA（作为附件或独立） | **vendor-agreement-review** → 数据保护部分的注释 |
   | 服务等级协议、SLA（作为附件） | **saas-msa-review** → SLA 部分的注释 |

   可能适用多个 skills。常见组合：
   - 主服务协议 + 个人信息处理协议附件 → vendor-agreement-review，并注明 PIPL 合规条款
   - SaaS 订阅 + 订单确认书 + SLA 附件 → saas-msa-review（覆盖所有三个）
   - 框架协议 + 带自动续期的订单确认书 → vendor-agreement-review + saas-msa-review 覆盖层

   当阅读标题后结构真正模糊时（例如，标题为"协议"且未列出附件的文档），阅读正文的前两页以解决它——然后停止并路由。

5. **如果启用，则确认路由。**

   如果 `confirm_routing` 为 `true`（或字段不存在）：

   ```
   我打算将其审查为：[协议类型]。

   已识别的文档：
   - [主协议标题] → [skill]
   - [附件A标题] → [将如何处理]
   - [附件B标题] → [将如何处理]

   正确吗？（是 / 否 — 或者告诉我哪里错了）
   ```

   在继续之前等待确认。如果用户更正路由，应用他们的指令并继续。

   如果 `confirm_routing` 为 `false`：静默继续。在审查备忘录顶部记录路由决策，以便用户可以看到应用了什么。

6. **运行 skill。** 完全遵循每个 skill 的工作流。如果适用多个 skills，按顺序运行它们并将输出集成到单个备忘录中——不要生成单独的备忘录。

7. **检查升级：** 如果任何问题根据 `~/.prismclaw/plugins/legal-chineselaw/commercial-legal/PRISMCLAW.md` 矩阵超出审查者的权限，调用 **escalation-flagger** 路由并起草请求。

8. **提供后续选项：**
   - 给业务负责人的利益相关者摘要
   - 带有修订标记的红线 .docx
   - [合同管理系统] 记录创建（如果已连接）
   - 添加到续约登记册（如果发现自动续期条款）

## 配置 confirm_routing

添加到 `~/.prismclaw/plugins/legal-chineselaw/commercial-legal/PRISMCLAW.md` → `## 审查偏好`：

```markdown
## 审查偏好

confirm_routing: true   # 设置为 false 以跳过路由确认并自动继续
```

冷启动访谈应该询问此偏好。默认为 `true`——已确认开启。随着信任建立，用户可以将其设置为 `false`。

## 示例

```
/commercial-legal:review vendor-msa.pdf
```

```
/commercial-legal:review https://example.collab.local/file/ABC123
```

```
/commercial-legal:review
[粘贴协议文本]
```

## 输出

根据 skill 格式的完整审查备忘录。路由决策记录在顶部。偏差逐偏差、具体红线语言、命名审批人。保存在 `~/.prismclaw/plugins/legal-chineselaw/commercial-legal/PRISMCLAW.md` → 内部风格说工作产品去的地方。


> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
