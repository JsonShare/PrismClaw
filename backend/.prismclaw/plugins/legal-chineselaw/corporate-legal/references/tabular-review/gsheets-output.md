<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->

# Google Sheets 输出规范

面向使用 Google Workspace 的团队。与 Excel 输出结构相同，但机制不同。如果 Excel 和 Sheets 路径都可用，询问用户喜欢哪个——不要从你的环境推断。

## 如何写入

三条路径，按优先顺序：

1. **Google Sheets MCP**（如果连接了具有写入/创建能力的 `gdrive` 或 `gsheets` MCP）。创建电子表格，写入工作表，通过 API 设置格式。
2. **通过 ADC 的 Google Sheets API**（如果用户已设置 `gcloud auth application-default login --enable-gdrive-access` 并安装了 Python `google-api-python-client`）。使用 `sheets.spreadsheets().create()` 和 `batchUpdate` 进行格式化。
3. **备用：CSV + 手动导入。** 写入 CSV，告诉用户导入到 Sheets。还写入 `format-instructions.md`，以便他们可以手动应用颜色编码和数据验证。

不要假设你未验证的写入权限。先检查；优雅地退回。

## 工作簿结构

与 Excel 规范完全对应——相同的工作表、相同的语义，Sheets 原生机制：

**工作表：`Review`**（主网格）
- 第 1 行：工作产品标题（合并单元格）
- 第 2 行：列标签
- 第 3 行以下：每份文件一行
- A 列：文件名/链接（如果源文件在 Drive 中，链接到文件——这是 Sheets 相对于 Excel 的优势）
- B 列以后：每个模式列一列
- **来源引用放入单元格备注**（Sheets 备注，不是评论——备注是持久注释，评论是协作线程）。备注在悬停时显示，导出到 `.xlsx` 时显示为评论。
- 按状态单元格填充：默认 = `answered`，浅黄色 = `unclear` 或 `needs_review`，浅灰色 = `not_present`。在 `batchUpdate` 中使用 `repeatCell` 和 `userEnteredFormat.backgroundColor`。
- 每组后有一个 `Verified` 列：默认空白，通过 `setDataValidation` 设置下拉数据验证 `✓ | ✗ | ?`。

**工作表：`Flags`**
- 与 Excel 规范相同。每个标记的单元格一行。

**工作表：`_schema`**
- 来自 `.review-schema.yaml` 的列定义。

**工作表：`_summary`**
- 计数、标记的列、验证提醒。

## 可利用的 Sheets 特定优势

- **源文件超链接。** 如果审查的文件在 Drive 中（常见于 VDR 导出和内部存储库），每行的文件名应该是指向文件的超链接。这是点击即源的模式，Sheets 原生支持。
- **共享审查。** Sheets 比本地 `.xlsx` 更好地处理并发审查。如果交易团队想要分配验证工作，这是要使用的格式。
- **模式的命名范围。** 为每列定义命名范围，使下游公式（数据透视表、条件计数）可读。
- **按状态列的条件格式。** 如果你为每个数据列写入隐藏的 `_state` 列，你可以用条件格式规则从它驱动颜色编码——比每单元格格式更干净，并且在排序后仍然有效。

## Sheets 特定注意事项

- **备注是每单元格的，在打印中不可见。** 如果输出将被打印或用于合伙人会议的 PDF，还要将引用写入 `Flags` 工作表，以便它们在打印中存活。
- **Sheets 有 1000 万单元格限制。** 在法律审查中不会达到，但如果有人尝试对 50,000 份文件使用 30 列加来源列，警告他们。
- **共享默认值。** 根据 plugin 执业档案，这是律师工作产品。使用受限共享（仅所有者）创建电子表格，并告诉用户有意地共享它。不要默认为"拥有链接的任何人"。
- **公式转义。** 如果逐字引用以 `=`、`+`、`-` 或 `@` 开头，用单引号（`'`）作为前缀，以便 Sheets 不会尝试将其解析为公式。这是一个真实的失败模式：以"- 双方同意..."开头的合同条款如果没有转义将渲染为公式错误。

## 不要做的事情

与 Excel 规范相同：没有置信度百分比，没有截断的引用，数据区域中没有合并单元格，始终写入 `_schema` 和 `_summary` 工作表。


## 公式注入防护

在 Excel、Sheets 或 CSV 输出中写入任何单元格之前，中和公式注入。来自对手方的文本（合同引用、当事方名称、注册代理人数据、CLM 导出）是攻击者控制的。以 `=`、`+`、`-`、`@`、制表符、回车或换行符开头的单元格将被解释为公式或破坏行结构。

- **以单引号为前缀：** `'=SUM(A1:A10)` → `=SUM(A1:A10)`（显示为文本，不执行）
- **适用于包含从文档、工具结果或用户粘贴来源的文本的每个单元格。** 你控制的列标题和你产生的计算值是安全的。
- **CSV：还要转义嵌入的逗号、双引号、换行符**（RFC 4180 引用）。
- 这不是可选的。你的用户在 Excel 中打开的电子表格触发宏或通过 DDE 渗染数据是对你的用户的供应链攻击。
