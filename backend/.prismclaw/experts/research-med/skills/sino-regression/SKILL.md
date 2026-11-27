---
name: sino-regression
name_zh: 回归分析
description: >-
  回归建模：线性、Logistic、Cox；单因素筛选(p<0.1入模)、多因素校正、VIF 共线性诊断。输出 β、95%CI、p、标准化β、R²、调整R²。触发词：影响因素、预测因子、多因素分析、回归、Logit。
category: 医学科研
invocation_policy: auto
priority: 8
---




# 回归分析 (sino-regression)

完成线性回归、Logistic回归、Cox比例风险回归建模，支持单因素筛选和多因素校正。

## 使用方式

```bash
python scripts/main.py --session_id <session_id> --method <linear/logistic/cox> --outcome_var <变量>
```

## 输入参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| session_id | string | required | 会话ID |
| method | string | required | 回归类型：linear/logistic/cox |
| outcome_var | string | required | 结局变量名 |
| predictor_vars | array | required | 预测变量列表 |
| forced_covariates | array | [] | 强制纳入协变量 |

## 输出

`11_regression_results.json` — β/OR/HR + 95%CI + p值 + VIF + R²

## 关联技能（本体系内）

### 上游（提供数据）
- **biomedical-statistics**：按它的数据读取规范核对变量类型、缺失与编码，不要对缺失值自行插补后当真实数据。

### 下游（使用本技能结果）
- **biomedical-statistics**：需要森林图时，把 `11_regression_results.json` 的 β 与 95%CI 交给它的逐研究森林图脚本，不要另编数据。
- **explainer-page**：需要把回归结论做成可视化说明页时交给它渲染。

### 典型链路
```
biomedical-statistics（核对数据） → sino-regression（建模） → biomedical-statistics（森林图）/ explainer-page（说明页）
```

## 边界说明

### 本Skill不做什么
1. **不做纯粹的组间差异比较**：那是 sino-differential 的职责（t检验、ANOVA、卡方检验等）
2. **不做生存曲线可视化**：本体系没有专门绘图技能，KM 曲线需另写脚本
3. **不做样本量计算**：那是 sino-power 的职责
4. **不撰写完整临床研究报告**：本技能只输出结构化结果 JSON

### Cox回归的定位
- 本Skill的method参数支持 `cox`，用于多因素生存分析建模
- 如果用户只要 Kaplan-Meier 曲线和 log-rank 检验，用 lifelines 另写脚本，并说明这是新增计算
- 如果用户已有明确的时间至事件数据要建模预测因子，使用本Skill的Cox功能

## 自包含声明

**本Skill已完全自包含**，不依赖外部 `shared/` 目录。

嵌入的共享模块位于 `scripts/lib/`，包括：
- `stats`: Cohen's d、η²、Cramér's V、Bonferroni、BH、置信区间等统计函数（用于效应量计算和多重比较校正）
- `utils`: 格式化、JSON解析等工具函数

回归分析中的效应量计算和p值校正功能依赖这些嵌入式共享模块。
