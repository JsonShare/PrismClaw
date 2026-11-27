# 单因素方差分析与多重比较

用户要求单因素方差分析及组间多重比较时，使用 `method=one_way_anova`、`design=independent`。先从用户资料或原文核对实验单位及分组；重复测量、配对和多个实验因素需要相应设计，本入口不承接这些推断。

## 方法选择

- 没有指定方差模型时，使用默认的 `variance_model=unequal`：Welch 单因素 ANOVA 与 Games–Howell 多重比较。报告明确实际方法，不宣称等方差成立。
- 用户明确要求普通 ANOVA、Tukey 或复现相应原研究方法时，使用 `variance_model=equal`：普通单因素 ANOVA 与 Tukey 比较；样本量不等时由 SciPy 使用 Tukey–Kramer。说明等方差是本次方法假设。
- 多个指标确有不同的方差模型时，用 `variance_model_by_panel` 为每个面板明确填写 `equal` 或 `unequal`，例如 `{"指标A":"equal","指标B":"unequal"}`。沿用原始面板名称，且方案中只填写全局或分面板模型之一。图和报告按每个面板实际执行的方法标注。

两种方法均依赖独立观测及组内近似正态分布。检查原始点、异常值和研究设计；小样本下，正态性或方差检验不显著不能证明前提成立。方法选择依据任务和设计，不按前提检验的 P 值机械切换。已有明显不适用证据时，说明具体问题并澄清受影响的推断。

每个面板的全部组间比较构成一个比较族，调整后 P 值及同时置信区间在该族内控制多重比较；不同指标之间没有联合校正。用户指定对照组比较或其他比较族时，需要匹配该要求的分析方案，不用全部两两比较代替。

## 执行与结果

沿用 `analyze.py --xlsx/--csv --plan --out-root --plot`，输入字段见[方案字段](plan-fields.md)，流程和交付见技能正文。`alpha` 默认 0.05。

`result.json` 保存整体方差口径、各面板的 `omnibus` 总体检验与 `comparisons`。混合方案的整体 `variance_model` 为 `mixed`，各面板行记录实际模型。`omnibus.csv` 保存方法、F 统计量、分子与分母自由度、P 值、组数和有效观测数。`comparisons.csv` 保存全部组对、A−B 均值差、调整后 P 值和区间。多重比较直接返回调整后 P 值，`p_value=null` 表示未另算未经调整的 P 值。

图形展示全部原始点、均值±样本 SD、总体检验结果及组间调整后 P 值；报告使用同一结果。`reproduction.zip` 包含完整输入、来源、方案、计算模块和复现入口，程序运行时已在临时目录重跑并核对。

计算使用 [SciPy f_oneway](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.f_oneway.html) 和 [SciPy tukey_hsd](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.tukey_hsd.html)，运行版本为依赖文件固定的 1.17.1。
