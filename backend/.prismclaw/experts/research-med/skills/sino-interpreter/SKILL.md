---
name: sino-interpreter
name_zh: 统计结果通俗化解读
description: 将统计结果转化为通俗语言（Plain Language）。触发词：「解释一下结果」「这个p值什么意思」「效应量大不大」「通俗话说」「写论文结论」。注意：不执行统计分析（那是上游分析Skills）、不生成图表（那是chart-generator）、不撰写完整报告（那是report-export）。两种场景：论文结果撰写（专业但易懂）、医患沟通（完全通俗）。效应量优先于P值，禁止「证明/证实/显著」等词。
category: 医学科研
invocation_policy: auto
priority: 8
version: 1.0.1
---

# Plain Language解释 (sino-interpreter)

将统计检验结果转化为患者可读的通俗语言，强制效应量优先、置信区间量化。

## 使用方式

```bash
python scripts/main.py --session_id <session_id> --results_path <结果路径> --plain_language_level <大众/患者/医生>
```

## 输入参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| session_id | string | required | 会话ID |
| results_path | string | required | 统计结果文件路径 |
| plain_language_level | string | 患者 | 通俗程度：大众/患者/医生 |

## 输出

`plain_language_interpretation.md` — 通俗化结果解释文本

## 关联Skills（全链路调用）

### 上游（提供需要解释的统计结果）
- **sino-differential**: 差异分析结果（t检验、卡方检验、ANOVA等）
  调用方式：`/sino-interpreter --session_id {session_id} --results_path 10_differential_results.json`
  数据流：10_differential_results.json → plain_language_interpretation.md

- **sino-regression**: 回归分析结果（β、OR、HR等）
  调用方式：`/sino-interpreter --session_id {session_id} --results_path 11_regression_results.json`
  数据流：11_regression_results.json → plain_language_interpretation.md

- **sino-survival**（本环境未随附该技能）: 生存分析结果（中位生存时间、HR等）；若上游以同名数据桩提供 `12_survival_results.json`，本技能仍可解读
  调用方式：`/sino-interpreter --session_id {session_id} --results_path 12_survival_results.json`
  数据流：12_survival_results.json → plain_language_interpretation.md

- **sino-power**: 样本量计算结果
  调用方式：`/sino-interpreter --session_id {session_id} --results_path power_analysis_result.md`
  数据流：power_analysis_result.md → plain_language_interpretation.md

### 下游（使用本skill的解释文本）
- **sino-report**: 将Plain Language解释嵌入最终报告
  调用方式：`/sino-report --session_id {session_id} --include_interpretation true`
  数据流：plain_language_interpretation.md → report.docx

- **sino-csr**: 将解释填充到CSR的Results章节
  调用方式：`/sino-csr --session_id {session_id} --report_type csr`
  数据流：plain_language_interpretation.md → 20_csr_report.md

### 典型链路
```
{分析skill} → sino-interpreter → sino-report / sino-csr
```

## 边界说明

### 本Skill不做什么
1. **不执行统计分析**：那是上游分析Skills的职责
2. **不生成图表**：图表生成不在本技能职责内（由上游或外部脚本完成）
3. **不撰写完整报告**：那是 sino-report / sino-csr 的职责
4. **不进行数据清洗/预处理**：数据清洗/预处理不在本技能职责内（由上游完成）

### 本Skill的定位
- **解释已有结果**：接收上游分析的结果（t检验、回归、生存分析等）
- **Plain Language输出**：将统计术语转化为通俗语言
- 是分析链路的"最后一公里"——让医生和患者理解结果

### 触发关键词
- "帮我解释一下这个结果"
- "用通俗的话说明一下"
- "这个p值是什么意思"
- "效应量大不大"
- "写论文的结论部分"

### 两种输出场景
- **论文结果撰写**：专业但易懂，保留统计术语但附通俗解释
- **医患沟通**：完全通俗，无统计术语，可用于患者知情同意

## 自包含声明

**本Skill已完全自包含**，不依赖外部 `shared/` 目录。

嵌入的共享模块位于 `scripts/lib/`，包括：
- `stats`: Cohen's d、η²、Cramér's V、Bonferroni、BH、置信区间等统计函数
- `utils`: 格式化、JSON解析等工具函数
- `templates`: Plain Language 解释模板（对本地 `template_engine.py` 的补充）

本地 `scripts/template_engine.py`（TemplateEngine类）是完整的实现，`scripts/lib/templates.py` 作为函数形式的补充供参考。
