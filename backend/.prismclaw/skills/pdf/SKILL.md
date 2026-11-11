---
name: pdf
description: PDF全能处理：创建精美PDF（报告、合同、简历）、填写表单、重排版、解析提取、合并拆分、水印加密。适用于法律文书、商务文档、学术论文等正式交付物。
name_zh: PDF全能处理
category: 文档处理
---

# pdf

统一的 PDF 创建 / 填写 / 重排 / 解析 / 操作技能。默认中文输出（除非用户要求英文）。

**使用方式**：先按下表确定路由，再读取对应 reference 文档，然后执行。SKILL.md 仅负责路由，不含执行细节。

**环境变量** — 运行时自动注入，**永远使用这些变量而非硬编码路径**：

| Variable | Purpose |
|----------|---------|
| `$PRISMCLAW_WORKSPACE` | Current workspace root |
| `$PRISMCLAW_TMP_DIR` | Temp directory (workspace-scoped) |
| `$PRISMCLAW_OUTPUT_DIR` | Output directory (workspace-scoped) |

**Never use `/tmp/` or hardcoded absolute paths** — they escape the workspace and cause write errors.

## Routing

| 路由 | 能力 | Reference | 触发条件 |
|------|------|-----------|---------|
| **CREATE** | 通过 Typst 管线从零生成 PDF（可基于用户提供的素材） | references/create_pdf/create.md | 用户要新 PDF——含基于笔记/PDF/图片/参考资料生成的报告、提案、简历等；以及「空白 PDF」需求 |
| **REFORMAT** | 保留已有文档内容，重新设计排版为 PDF | references/reformat_pdf/reformat.md | 用户提供已有文档，要求**保留原内容**重新设计/排版为 PDF |
| **FILL** | 填写已有 PDF 的表单字段 | references/fill_pdf/fill.md | 用户要填写/补全已有 PDF 的表单字段 |
| **PARSE** | 提取 PDF 内容为 Markdown | references/parse_pdf/parse.md | 用户只要提取/转 Markdown/分析，不需要设计感 PDF 产出 |
| **TOOLKIT** | 合并/拆分/旋转/水印/加密解密/图片↔PDF | references/toolkit/toolkit.md | 用户要操作已有 PDF（merge/split/rotate/watermark/encrypt/decrypt/images2pdf/pdf2images） |


