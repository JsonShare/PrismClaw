<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->

# Excel 输出规范

Excel 文件是大多数交易团队实际会打开的可交付成果。把它做好。

## 如果 PrismClaw in Excel / Office agent 可用

通过 Office agent 直接在 Excel 中构建工作簿。这是首选路径，因为它保留格式，让审查者在其原生工具中工作，并原生支持单元格注释模式。

## 如果不可用，使用 openpyxl

用 `python3 -c "import openpyxl"` 检查。如果未安装，提供安装（`pip3 install openpyxl`）或退回到 CSV。

## 工作簿结构

**工作表 1：`Review`**（主网格）
- 第 1 行：工作产品标题（合并单元格，来自 plugin 配置 `## Outputs` 的标题）
- 第 2 行：列标签
- 第 3 行以下：每份文件一行
- A 列：文件名/路径
- B 列以后：每个模式列一列，按模式顺序
- 每个数据列之后，一个隐藏的 `_source` 列，包含 `[quote] | [location]`
- 数据列单元格上的单元格注释 = 引用和位置（即使隐藏了 `_source`，悬停时也能显示）
- 按状态单元格填充：无填充 = `answered`，`#FFF2CC`（浅黄色）= `unclear` 或 `needs_review`，`#EFEFEF`（浅灰色）= `not_present`
- 每组 [数据 + _source] 后有一个 `Verified` 列：默认空白。审查者填写。下拉验证：`✓`、`✗`、`?`。

**工作表 2：`Flags`**
- 每个标记为 `unclear` 或 `needs_review` 的单元格一行
- 列：文件、列、状态、值（如果有）、引用、位置、备注
- 这是验证工作队列。按列排序，以便审查者可以批量处理类似的判断。

**工作表 3：`_schema`**
- 来自 `.review-schema.yaml` 的列定义，每列一行：id、label、type、options、prompt
- 使文件自记录。六个月后打开它的合伙人可以看到确切询问了什么。

**工作表 4：`_summary`**
- 文件数量、列数量、运行日期
- 每列的已答复/不存在/不清楚/需要审查计数
- 规范化通过标记的列列表
- 验证提醒文本

## 不要做的事情

- 不要写置信度百分比列。它不是信息。状态 + 引用是信号。
- 不要截断引用以适应单元格。换行文本或将完整引用放在注释中。
- 不要合并数据区域中的单元格。律师将排序和筛选。
- 不要写没有 `_schema` 和 `_summary` 工作表的表格。自我记录是使文件可信的原因。


## 公式注入防护

在 Excel、Sheets 或 CSV 输出中写入任何单元格之前，中和公式注入。来自对手方的文本（合同引用、当事方名称、注册代理人数据、CLM 导出）是攻击者控制的。以 `=`、`+`、`-`、`@`、制表符、回车或换行符开头的单元格将被解释为公式或破坏行结构。

- **以单引号为前缀：** `'=SUM(A1:A10)` → `=SUM(A1:A10)`（显示为文本，不执行）
- **适用于包含从文档、工具结果或用户粘贴来源的文本的每个单元格。** 你控制的列标题和你产生的计算值是安全的。
- **CSV：还要转义嵌入的逗号、双引号、换行符**（RFC 4180 引用）。
- 这不是可选的。你的用户在 Excel 中打开的电子表格触发宏或通过 DDE 渗染数据是对你的用户的供应链攻击。
