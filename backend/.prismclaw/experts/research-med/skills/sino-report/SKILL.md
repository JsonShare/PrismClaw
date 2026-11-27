---
name: sino-report
name_zh: 结构化报告导出
description: 导出结构化报告。触发词：「导出报告」「生成论文报告」「按JAMA格式导出」「Word格式报告」。支持Lancet/NEJM/JAMA/BMJ/CMJ/中华系列等期刊格式。注意：不执行统计分析（那是分析Skills）、不解释结果（那是result-interpreter）、不生成图表（那是chart-generator）、不撰写CSR（那是csr-report-writer）。输出Word/PDF/Markdown格式。
category: 医学科研
invocation_policy: auto
priority: 8
version: 1.0.1
---

# 报告导出 (sino-report)

将统计分析结果自动填充到结构化报告模板，输出Markdown/Word/PDF格式，支持主流期刊引用格式声明。

## 使用方式

```bash
python scripts/main.py --session_id <session_id> --journal_style <期刊> --format <md/docx/pdf>
```

## 输入参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| session_id | string | required | 会话ID |
| report_template | string | default | 报告模板 |
| journal_style | string | Lancet | 期刊格式 |
| include_interpretation | bool | false | 是否含通俗解释 |
| format | string | md | 导出格式：md/docx/pdf |

## 输出

`report_*.md/.docx/.pdf` — 结构化分析报告，含方法描述 + 结果表格 + 图表 + 期刊声明

## 关联Skills（全链路调用）

### 上游（提供内容和图表）
- **sino-data-prep**（本环境未随附）: 数据质量报告内容（由上游数据准备脚本提供 04_quality_report.md）
  数据流：04_quality_report.md

- **sino-differential**: 差异分析结果表格
  数据流：10_differential_results.json

- **sino-regression**: 回归分析结果表格
  数据流：11_regression_results.json

- **sino-survival**（本环境未随附）: 生存分析结果（由上游生存分析脚本提供 12_survival_results.json）
  数据流：12_survival_results.json

- **sino-chart**（本环境未随附）: 所有生成的图表文件（由上游图表脚本提供 30_charts/*.tiff）
  数据流：30_charts/*.tiff

- **sino-interpreter**: 通俗语言解释（可选包含在报告中）
  数据流：plain_language_interpretation.md

### 下游
- **sino-csr**: CSR报告可调用本skill导出最终格式
  调用方式：`/sino-report --session_id {session_id} --format docx --journal_style ICH-E3`

### 典型链路
```
{任意分析skill} → sino-interpreter → sino-report（图表由上游提供并嵌入）
```

## 边界说明

### 本Skill不做什么
1. **不执行统计分析**：由上游分析技能（sino-differential / sino-regression 等）完成；生存分析本环境未随附独立技能，需上游脚本提供结果
2. **不解释结果**：那是 sino-interpreter 的职责
3. **不生成图表**：图表由上游生成或外部脚本提供，本技能只负责嵌入
4. **不撰写CSR**：那是 sino-csr 的职责（本skill输出的是学术期刊报告）

### 本Skill的定位
- **结构化报告导出**：将已有的分析结果填充到报告模板
- **期刊格式合规**：符合Lancet/NEJM/JAMA等期刊的格式要求
- **集成上游输出**：整合分析结果+图表+解释文本

### 触发关键词
- "导出报告"
- "生成论文用的报告"
- "按JAMA格式导出"
- "生成Word格式的报告"
