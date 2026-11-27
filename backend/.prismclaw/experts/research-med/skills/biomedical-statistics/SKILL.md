---
name: biomedical-statistics
name_zh: 生物统计分析
description: >
  按来源执行描述统计、单率样本量、t检验、单因素方差分析与多重比较、行向Z标准化。描述任务未给设计时用unknown，未知单位写未注明；不由表头、行号或重复数推定独立生物学重复。已给口径直接执行，可逆显示不追问。
  只对已给数据执行计算与绘图，不设计研究、不评级证据：统计与证据纪律交 `research-evidence`，研究方案交 `clinical-trial-protocol`，成图版式交 `medical-scientific-figures`。
category: 医学研究
invocation_policy: user_only
priority: 7
---

# 生物统计分析

按本轮原始资料计算和绘图，不补造设计、个案、数据或来源。数值、图和结果文字来自同一次实际运行，不手抄、不估算；未运行的计算标未执行。按用户或原题指定的方法和选项计算，做不到时说明缺口，不换方法沿用原名。程序成功只说明登记流程已执行，不证明研究前提或科学解释正确。

统计任务不调用todo_write建待办清单，按下列步骤直接执行。

## 按任务选择

- 只核对或改写已给句子、表格、Results：不调用工具，按[文字核对与方法讨论](references/detailed-statistics-workflow.md)的A部分作答。
- 设计讨论或文献参数规划：读同一文件的方法讨论部分，未要求计算不执行。
- 单率精度样本量（给定p、绝对容许误差和双侧alpha）：读[精度规划](references/proportion-planning.md)，运行proportion_sample_size.py --format markdown，直接交同次正文。
- 只展示文献RCT已报告的效应量和置信区间：读[逐研究森林图](references/study-forest.md)，用render_study_forest.py，不做新推断。
- 原始数据的描述统计、两组t检验、单因素方差分析与多重比较、行向Z：用analyze.py，按下方流程，不另写统计算法。
- 影响因素/预测建模（线性、Logistic、Cox 回归，含单因素筛选与多因素校正）：改用 `sino-regression` 技能，用它的 scripts/main.py，不在此处手写回归。
- 组间差异且需要非参数检验（Mann-Whitney、Kruskal-Wallis、Wilcoxon）或卡方/Fisher：改用 `sino-differential` 技能，它按正态性与方差齐性自动选法并给效应量。
- 样本量与检验效能（给定 d、alpha、power 反推 n）：改用 `sino-power` 技能；本技能只覆盖单率精度样本量。
- 统计结果解读（把 P 值、效应量、置信区间转成可发表的结论表述，含多重比较与亚组解读）：改用 `sino-interpreter` 技能。
- 生成期刊格式的统计报告（含结果表格、方法段落与复现包）：改用 `sino-report` 技能。
- 生成临床试验 CSR（临床研究总结报告）的统计部分：改用 `sino-csr` 技能。
- 生存分析（KM 曲线、log-rank、Cox）：本技能不支持。需要时用 lifelines 另写脚本，并说明这是新增计算而非本技能能力。
- 登记表外且没有对应技能的方法（ROC、单侧检验、纵向混合模型等）：说明未支持的具体方法和未执行范围，不临时手写替代算法，不改名套用表内method；不依赖该方法的读数、描述和绘图照常完成。

## analyze.py 流程

1. **读数据**：XLSX用 `inspect_workbook.py --input <附件> --sheet <表> --range <区域>` 逐格读取，按cell/value/kind定位；实测0保留，空白是缺失，无缓存值的公式不当空白；columnCounts只表示数值格数。CSV必需group、value列，可选panel、id、time。
2. **来源与设计**：
   - `source_origin`：文件名、附件或用户给出原论文标识时为published_data，否则为attached_data。published_data先按[原文获取](references/source-captions.md)读取本篇对应面板的图注和方法，再按[图注来源说明](references/source-notes.md)写caption_notes。
   - `design`按真实资料填independent、paired、longitudinal或unknown。配对需要真实ID，不由行号或相等n推定。要求检验但设计未知时，先用method=descriptive、design=unknown交付描述图、report.md和复现包，P值标待确认，交付后再用问答工具只问这一个设计问题。
   - `unit`只写量纲；来源未注明时写unknown，纵轴写“单位未注明”，可核原图坐标轴时先核原图。相对、归一化类轴标签需要原表单元格依据。
   - 只为真实分组、时点映射或推断设计的缺项提问。配色、布局、分面、P值位置等显示选择直接用清晰默认；用户已给的误差条、零值、缺失和排列直接执行。
3. **写方案并运行**：

   | method | 用途 |
   | --- | --- |
   | descriptive | 描述汇总，design可为independent、paired、longitudinal、unknown，不推断 |
   | two_sample_t | 两组t检验：independent解析为Welch，paired解析为配对t并核真实ID |
   | welch_t、paired_t、student_t | 用户明确指定时使用；student_t需equal_variance_confirmed=true及真实来源 |
   | pairwise_t_holm | 每面板全部两两比较，Holm调整P值 |
   | one_way_anova | 独立组总体检验与全部两两比较，见[单因素分析](references/one-way-anova.md) |
   | tukey_hsd | 需normal_equal_variance_confirmed=true |
   | row_z_descriptive | XLSX行向Z标准化与热图，见[行向Z](references/row-z.md) |

   均值差检验为双侧，alpha默认0.05。表外方法不改名套用表内method。最小方案：

   ```json
   {"source_origin":"attached_data","design":"independent","method":"two_sample_t","unit":"unknown","missing":"stop",
    "plot_labels":{"y":"Expression (unit not stated)"},
    "blocks":[{"sheet":"Data","panel":"Expression","group":"Ctrl","range":"B2:B6"},
              {"sheet":"Data","panel":"Expression","group":"KO","range":"C2:C6"}]}
   ```

   时间轴、数值横轴、分面纵轴、配对ID、分块和显示选项见[方案字段](references/plan-fields.md)；转录原表P值见[原表P值转录](references/reported-p.md)。运行 `<Python> <技能目录>/scripts/analyze.py --xlsx|--csv <输入> --plan plan.json --out-root <工作区子目录> --plot`。字段有误时程序停止并给出原因，按提示修改后重跑。
4. **核对与交付**：程序成功后输出运行目录、【交付】清单、【自动核对】（TIFF像素与DPI、图中坐标、复现包在临时目录重跑后与本次逐字节一致）和【report.md 全文】。
   - 用ViewImage看一次figure-preview.png（有热图再看heatmap-preview.png），检查标注、遮挡和裁切；对照输出中的report.md核对图注定义、单位和来源是否齐全、group_explanations与caption_notes.text是否重复。
   - 需要修改时改方案后重跑到新目录，不手改report.md或结果文件。只调整版式且已有单文件绘图程序时，按[同次发布最终重绘](references/renderer.md)在同一次运行中重绘。
   - 无需修改时present_files图（SVG、TIFF）、report.md和reproduction.zip，最终回复沿用【交付】正文并附文件链接。图注、结果和限制以report.md为准，聊天不另写一套数值、研究背景或复现结论；用户要求内联时逐字引用report.md。
   - 复现和图面核对已由程序完成：不再另读report.md、delivery.md或结果文件，不重跑reproduce.py，不另写校验脚本。
   - 同批还要写论文、PPT或申报材料时，只用本次实际结果，未执行的分析写明未执行，不由拟议方案倒填结果。

## 边界

- 原研究方法与本次计算分开写；原文SEM与本图SD分别说明，已给均值±SD时不换SEM。
- adjusted、unadjusted需要adjustment_evidence；只有P列或未注明校正时记unknown。
- 原文逐字校验不证明语义。指标、分子分母、材料、组别、时点逐项对应本面板，不借其他面板或材料的定义；标题只描述本次对象、指标和条件。
- 取证限本篇所需图注和方法；补充材料每次最多90秒、25 MiB，失败最多重试一次，记录未核项后继续可完成部分。真实科学缺项只暂停受影响的推断。
- 结果说明按同次比较记录顺序逐项转述，不按差值最大或P值最小自动选择研究重点。主比较、因素效应及设计适用性由真实任务与方案确定，不从组间差值大小反推；原比较数值、校正与完整区间保留。
- 只用当前解释器；依赖缺失时如实报告，不在任务内安装。
