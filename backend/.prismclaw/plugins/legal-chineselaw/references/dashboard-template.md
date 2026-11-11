<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->

# Dashboard Template

*由仪表板提供护栏引用。保持仪表板简单一致——价值在于理解速度，而非视觉上的精致。*

## 结构（从上到下）

1. **Title and metadata.** 这是什么、何时生成、涵盖什么内容。一行。
2. **Summary stats.** 重要的计数，颜色编码。"40 个发现：🔴 3 个阻塞 · 🟠 8 个高 · 🟡 15 个中 · 🟢 14 个低——本周 6 个到期。"这是最有价值的行。使其可扫描。
3. **The reviewer note.** 与任何输出相同的单块格式。来源、范围、标记、依赖前事项。仪表板不会跳过安全元数据。
4. **Chart(s).** 最多一个或两个。选择显示形状的那个：
   - **Risk distribution**（条形图）：按严重性的计数。用于发现、问题、标记。
   - **Category breakdown**（饼图或堆叠条形图）：按类型的计数。用于 OSS 许可证、合同类型、事项类别。
   - **Timeline**（轻量级甘特图或排序表）：按顺序的日期。用于续约登记、截止日期追踪、结束检查清单。
   - 永远不要超过两个。有五个图表的仪表板是报告，报告比表格更难阅读。
5. **The table.** 可排序、可过滤、按严重性/状态颜色编码。列：原始输出中的那些，修剪到适合屏幕的内容。最后放一个"details"或"notes"列——这是被截断的那个。
6. **The decision tree.** 与文本输出相同的选项。"下一步是什么？"

## 按展示界面渲染

- **Cowork / PrismClaw Desktop:** HTML 工件。自包含、单个文件、内联 CSS。无外部依赖、无 CDN、无 npm。表格：带有 `data-sort` 属性和小型内联 JS 排序器的 HTML `<table>`。图表：条形图的内联 SVG 或 Unicode 块字符。保持 JS 最小——排序和过滤，没有其他。
- **PrismClaw:** 将相同的 HTML 文件写入 plugin 的输出文件夹（`~/.prismclaw/plugins/legal-chineselaw/<plugin>/outputs/dashboard-<topic>-<date>.html`）并告诉用户打开它：macOS 上的 `open <path>`，或"在浏览器中打开。"还生成带有 Unicode 块图表的 markdown 版本用于摘要统计，以便用户在不离开终端的情况下看到形状。
- **Excel (optional, where it fits):** 对于 `tabular-review`、`renewal-tracker`、`entity-compliance`，以及用户将带入会议或与非技术利益相关者共享的任何内容。使用现有的 Excel 输出规范。应用公式注入防护。
- **Escape untrusted input (apply every dashboard, every time).** 来自此次会话外部的每个值——第三方清单中的 OSS 包/许可证字段、对方合同文本、尽职调查发现、供应商名称、事项描述、任何用户或 VDR 提供的字符串——在进入文档之前必须进行 HTML 转义。在写入表格单元格、摘要行、图表标签和工具提示文本时，将 `&`、`<`、`>`、`"`、`'` 转义为实体。在内联 JS 排序器/过滤器中，通过 `textContent` 设置单元格文本，永远不要通过 `innerHTML`。不要发出其内容插值不可信字符串的 `<script>` 块。在没有方案检查（仅 `http:` / `https:` / `mailto:`）的情况下，不要将不可信 URL 渲染到 `href` 或 `src` 中。这是 Excel 侧公式注入防护的 HTML 表面等效物——相同的威胁（攻击者控制的单元格内容），不同的执行表面（浏览器 JS 而非电子表格公式）。审阅者在浏览器中打开的仪表板是信任边界；像对待信任边界一样对待它。

## 保持简洁朴素

- **Color palette:** 红色 / 橙色 / 黄色 / 绿色用于严重性。灰色用于中性。蓝色用于状态。没有其他。
- **No animations, no frameworks, no external fonts.** 离线时崩溃的仪表板就是崩溃了。
- **No clever layouts.** 摘要、审阅者注释、图表、表格、决策树。从上到下。每个仪表板看起来都一样，因此读者知道在哪里看。
- **The markdown version matters.** 一些用户在终端中，不会打开浏览器。带有 Unicode 条形的摘要统计行（例如 `🔴 ███ 3  🟠 ████████ 8  🟡 ███████████████ 15  🟢 ██████████████ 14`）给他们形状。
