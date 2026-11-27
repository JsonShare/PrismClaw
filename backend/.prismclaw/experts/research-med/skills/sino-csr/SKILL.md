---
name: sino-csr
name_zh: 临床研究报告CSR
description: 按照ICH E3规范撰写临床研究报告（CSR），用于药品注册申报。触发词：「CSR报告」「ICH E3」「NMPA申报」「临床研究报告」「FDA提交」「药品注册」。自动填充19章节结构，支持中文NMPA和英文FDA两种模式。注意：不执行统计分析（那是分析Skills）、不生成图表（那是chart-generator）。需要上游分析结果作为输入。
category: 医学科研
invocation_policy: auto
priority: 8
version: 1.0.1
---

# 临床研究报告撰写 (csr-report-writer)

按照ICH E3指南规范撰写临床研究报告（CSR），自动从统计分析结果中提取数据填充到标准结构中，支持中文NMPA提交和英文FDA提交两种模式。

## 概述

CSR（Clinical Study Report）是药物临床试验的核心提交文件，用于药品注册申报。本skill根据ICH E3《临床研究报告的结构与内容》指南（CPMP/ICH/288/95），自动将上游分析结果填充到标准19章节结构中，生成符合NMPA或FDA要求的CSR文档。

## 使用场景

- III期临床试验完成后需要撰写CSR进行NMPA申报
- 需要按照ICH E3标准格式整理临床试验数据
- 多个分析结果需要整合为一份完整的临床研究报告
- 需要中英双语CSR（NMPA中文版 + FDA英文版）

## 触发关键词

医生口语化表达示例：
- "帮我写临床研究报告"
- "CSR报告"
- "ICH E3报告"
- "药品注册报告"
- "临床试验报告"
- "NMPA申报材料"
- "我需要提交CSR"
- "写一份III期试验的报告"

## 使用方式

```bash
python scripts/main.py --session_id <session_id> --report_type csr --study_phase phase3 --lang zh --output_format md
```

## 输入参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| session_id | string | required | 会话ID（用于数据桩路径） |
| report_type | string | csr | 报告类型：csr（最终报告）/ interim（期中报告）/ safety（安全性报告） |
| study_phase | string | required | 研究分期：phase1/phase2/phase3/phase4 |
| lang | string | zh | 语言：zh（中文NMPA）/ en（英文FDA） |
| output_format | string | md | 输出格式：md/docx/pdf |
| protocol_id | string | - | 方案编号（手动补充） |
| study_design | string | - | 研究设计类型（自动检测或手动指定） |

## 输入数据

从共享数据桩读取所有上游分析结果：

| 文件 | 来源Skill | 用途 |
|------|----------|------|
| 01_prepared_data.csv | 上游数据准备（未随附） | 受试者数量、基线数据 |
| 02_data_dictionary.md | 上游数据准备（未随附） | 变量定义和编码说明 |
| 04_quality_report.md | 上游数据准备（未随附） | 数据质量章节 |
| 10_differential_results.json | sino-differential | Table 1基线比较、次要终点 |
| 11_regression_results.json | sino-regression | 多因素分析、疗效影响因素 |
| 12_survival_results.json | 上游生存分析（未随附） | KM曲线、HR值 |
| power_analysis_result.md | sino-power | 样本量计算依据 |
| 30_charts/*.tiff | 上游图表（未随附） | 图表嵌入 |
| plain_language_interpretation.md | sino-interpreter | 讨论章节的通俗解释 |

## 输出

生成到共享数据桩：
- `20_csr_report.md` — CSR Markdown报告
- `20_csr_report.docx` — CSR Word文档（如指定 --output_format docx）
- `20_csr_report.pdf` — CSR PDF文档（如指定 --output_format pdf）

## 关联Skills（全链路调用）

本skill是全链路的终端汇聚节点，可自动调用以下skills完成全链路CSR撰写：

### 上游（数据准备与分析）
- **data-preparation**: 清洗原始数据，提供基线数据和质量报告
  调用方式：`/data-preparation --session_id {session_id} --mode quick`
  输入：raw_data.csv → 输出：01_prepared_data.csv, 02_data_dictionary.md, 04_quality_report.md

- **differential-analysis**: 生成人口学特征基线比较（Table 1）和组间差异
  调用方式：`/differential-analysis --session_id {session_id} --groupby_vars treatment --compare_vars age,gender,bmi`
  输出：10_differential_results.json

- **regression-analysis**: 多因素分析（疗效影响因素）
  调用方式：`/regression-analysis --session_id {session_id} --method logistic --outcome_var 结局变量 --predictor_vars age,gender,treatment`
  输出：11_regression_results.json

- **survival-analysis**: 生存分析（KM曲线、Log-rank、Cox回归）
  调用方式：`/survival-analysis --session_id {session_id} --time_var os_time --event_var os_event`
  输出：12_survival_results.json

- **power-analysis**: 样本量计算依据（填充统计方法章节）
  调用方式：`/power-analysis --session_id {session_id} --test_method two_sample_ttest --effect_size 0.5 --power 0.8`
  输出：power_analysis_result.md

- **result-interpreter**: 将统计结果转化为Plain Language解释（填充讨论章节）
  调用方式：`/result-interpreter --session_id {session_id} --results_path 10_differential_results.json --plain_language_level 医生`
  输出：plain_language_interpretation.md

- **chart-generator**: 生成CSR所需的所有图表（KM曲线、森林图等）
  调用方式：`/chart-generator --session_id {session_id} --chart_type km --journal_style Lancet --lang cn`
  输出：30_charts/*.tiff

### 下游（最终导出）
- **report-export**: 最终输出Word/PDF格式
  调用方式：`/report-export --session_id {session_id} --format docx --journal_style ICH-E3`
  输出：report_csr.docx

### 完整CSR全链路
```
data-preparation → differential-analysis → regression-analysis → survival-analysis
       ↓                    ↓                    ↓                  ↓
  power-analysis → chart-generator → result-interpreter → csr-report-writer
                                                              ↓
                                                        report-export
```

## ICH E3标准章节结构（19模块）

| 序号 | 模块 | 数据来源 | 自动化程度 |
|------|------|---------|-----------|
| 1 | 研究报告基本信息 | 手动补充 | 半自动 |
| 2 | 声明 | 自动检测研究类型 | 全自动 |
| 3 | 研究者及研究机构 | 手动补充 | 手动 |
| 4 | 引言 | 手动补充 | 手动 |
| 5 | 研究目的 | 手动补充 | 手动 |
| 6 | 研究设计与方法 | data-preparation + power-analysis | 半自动 |
| 7 | 研究受试者 | data-preparation | 全自动 |
| 8 | 有效性分析 | differential + regression + survival | 全自动 |
| 9 | 安全性分析 | data-preparation | 半自动 |
| 10 | 个体受试者数据 | data-preparation | 半自动 |
| 11 | 疗效分析详情 | differential + regression | 全自动 |
| 12 | 安全性分析详情 | data-preparation | 半自动 |
| 13 | 讨论与结论 | result-interpreter | 半自动 |
| 14 | 表格与图表列表 | chart-generator | 全自动 |
| 15 | 参考文献 | 手动补充 | 手动 |
| 16 | 附录 | 手动补充 | 手动 |
| 17 | 补充信息 | 手动补充 | 手动 |
| 18 | 数据质量与质量控制 | data-preparation | 全自动 |
| 19 | 最终报告签署页 | 手动补充 | 手动 |

## Plain Language对话示例

**场景：用户需要撰写III期CSR**

```
医生：我需要写一个III期临床试验的CSR报告，给NMPA申报用

AI：好的！我来帮您按照ICH E3规范撰写临床研究报告。

    首先确认几个信息：
    1. 您的试验数据已经完成统计分析了吗？
       - 是的，分析结果都有了
       - 还没有，需要先做分析

    2. 研究阶段：
       - II期临床试验
       - III期临床试验
       - IV期临床试验

    3. 提交语言：
       - 中文（NMPA）
       - 英文（FDA/EMA）

医生：分析做完了，III期，中文提交

AI：明白了！我将按照以下流程生成您的CSR报告：

    第1步：从共享数据桩读取所有分析结果
    ├── 10_differential_results.json（基线比较）
    ├── 11_regression_results.json（疗效分析）
    └── 30_charts/（KM曲线、森林图等）

    第2步：自动填充ICH E3标准19个核心模块
    ├── 1. 研究概要（手动补充方案信息）
    ├── 2. 声明（自动检测 → CONSORT声明）
    ├── 3. 研究者及机构（手动补充）
    ├── 4. 引言（手动补充）
    ├── 5. 研究目的（手动补充）
    ├── 6. 研究设计与方法（半自动，power-analysis提供样本量依据）
    ├── 7. 研究受试者（全自动，从data-preparation提取）
    ├── 8. 有效性分析（全自动，从differential+regression+survival提取）
    ├── 9. 安全性分析（半自动，需要AE数据）
    ├── 10-12. 详情章节（半自动）
    ├── 13. 讨论与结论（半自动，result-interpreter提供通俗解释）
    └── 14-19. 列表/附录/签署（手动补充）

    第3步：生成图表目录和参考文献
    第4步：输出Markdown格式最终文档

    预计输出：20_csr_report.md（符合NMPA ICH E3要求）

    ---
    [开始生成CSR]  [查看ICH E3模板]  [补充缺失数据]
```

## 参考文档

- [ICH E3中文模板](templates/ich_e3_template.md)

## 自包含声明

**本Skill已完全自包含**，不依赖外部 `shared/` 目录。

嵌入的共享模块位于 `scripts/lib/`，包括：
- `stats`: Cohen's d、η²、Cramér's V、Bonferroni、BH、置信区间等统计函数
- `utils`: 格式化、JSON解析等工具函数

这些模块主要用于结果格式化输出，支持CSR报告中的统计量展示规范。
