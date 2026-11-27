---
name: sino-power
name_zh: 样本量计算
description: >-
  样本量计算：两样本t检验、卡方、Log-rank 的样本量与检验效能互推，支持优效/等效/非劣效设计与分组比例，默认按 15% 失访率上修。触发词：计算样本量、需要多少患者、样本量够不够、power、效能分析。
category: 医学科研
invocation_policy: auto
priority: 8
---


# 样本量计算Skill

本Skill计算研究设计所需的最小样本量，支持多种检验方法和研究设计类型。

## 使用方式

```bash
python scripts/main.py --session_id <session_id> --test_method two_sample_ttest --effect_size 0.5 --power 0.8
```

## 输入参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| test_method | string | required | 检验方法：two_sample_ttest, chi_square, logrank |
| design_type | string | superiority | 设计类型：superiority, equivalence, non_inferiority |
| effect_size | float | required | 效应量 |
| alpha | float | 0.05 | 显著性水平 |
| power | float | 0.8 | 统计效能 |
| group_ratio | string | 1:1 | 分组比例 |
| loss_to_followup | float | 0.15 | 失访率 |

## 输出

缺省写入 `<cwd>/session-<session_id>/power_analysis_result.md`，可用 `--output_path` 指定。

## 关联技能（本体系内）

### 上游（拿参数）
- **biomedical-statistics**：给的效应量、标准差、事件率等参数按它的数据读取规范核对，避免用推测值算样本量。

### 下游（用本技能的样本量结果）
- **sino-regression / sino-differential**：样本量确定后做实际建模与差异检验。
- **medical-ppt / explainer-page**：需要效应量-样本量关系图时，把本技能输出的数值表交给它们作图，不要自行编造曲线数据。

### 典型链路
```
biomedical-statistics（核对参数） → sino-power（算 n） → sino-differential / sino-regression（分析）
```

## 边界说明

### 本Skill不做什么
1. **不做实际的统计分析**：那是 sino-differential / sino-regression 的职责
2. **不做生存曲线与图表**：本体系没有独立绘图技能；需要图时把数值表交给 explainer-page，或说明只能给数值
3. **不撰写完整报告**：本技能只产出样本量计算报告，整篇论文报告由用户或对应写作技能完成
4. **不做生存分析的建模部分**：Log-rank 样本量在本技能覆盖，KM 曲线与 Cox 建模需另用 lifelines 写脚本并说明是新增计算

### 本Skill的定位
- **研究设计阶段使用**：在收集数据之前计算需要多少样本量
- **也可用于事后Power计算**：根据已有数据计算检验效能
- 仅计算样本量/效能，不执行实际的数据分析

### 适用方法
- 两样本t检验（连续变量两组比较）
- 卡方检验（分类变量率比较）
- Log-rank检验（生存分析）
- 不支持：复杂设计（重复测量、混合效应模型等）需要专业统计软件