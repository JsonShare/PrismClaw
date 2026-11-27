---
name: sino-differential
name_zh: 组间差异分析
description: >-
  组间差异比较：自动判断正态性与方差齐性后选 t 检验、Mann-Whitney、Kruskal-Wallis 或卡方/Fisher，附 Cohen's d、Hedges' g、η² 等效应量与多重比较校正。触发词：组间差异、两组比较、非参数检验、卡方、效应量。
category: 医学科研
invocation_policy: auto
priority: 8
---




# 差异分析Skill

本Skill执行分组数据的差异分析，支持自动方法选择和多重比较校正。

## 使用方式

```bash
python scripts/main.py --session_id <session_id> --groupby_vars <json_array> --compare_vars <json_array> --test_type auto
```

## 输入数据

从共享数据桩读取：
- `01_prepared_data.csv`：清洗后的数据
- `02_data_dictionary.md`：数据字典

## 输出

生成到共享数据桩：
- `10_differential_results.json`：差异分析结果

## 关联技能（本体系内）

### 上游（提供数据）
- **biomedical-statistics**：按它的数据读取规范核对分组编码、缺失与单位，不对缺失值插补后当真实数据。

### 下游（使用本技能结果）
- **sino-regression**：差异显著后要控制混杂、看影响因素时转回归。
- **explainer-page**：需要把分组差异做成可视化说明页时交给它渲染。

### 典型链路
```
biomedical-statistics（核对数据） → sino-differential（差异） → sino-regression（多因素）/ explainer-page（说明页）
```

## 边界说明

### 本Skill不做什么
1. **不做回归建模**：那是 sino-regression 的职责（线性/Logistic/Cox回归）
2. **不做生存分析**：本体系没有生存分析技能，需另写脚本
3. **不做样本量计算**：那是 sino-power 的职责
4. **不做纯粹的预测/影响因素分析**：那是回归分析的工作

### 本Skill的定位
- **横向比较**：比较两组或多组之间的差异（有分组变量）
- **不需要时间维度**：结局是即时获得的（如疗效评分、血压值、有效/无效）
- 如果研究目的是"看哪些因素影响结局"但没有明确的时间维度，请用 sino-regression
- 如果研究目的是"看因素如何预测时间至事件"，本体系不支持，需说明并另行处理