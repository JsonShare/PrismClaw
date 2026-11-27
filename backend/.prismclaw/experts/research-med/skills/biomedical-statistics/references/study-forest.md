# 逐研究森林图：只展示已报告结果

用于将已核实原始RCT报告的效应量及其置信区间画成森林图。不重新估计效应或区间，不执行Meta分析，不生成合并菱形、权重或异质性。用户要求Meta分析时不能将本入口冒称为已完成Meta分析。

## 取数与去重

逐条核对原始研究的试验身份、研究类型、人群、结局定义、随访时点、效应量类型、是否调整及区间置信水平；记录实际读到的来源和页/表/图位置。试验的多个报告使用同一study_id。同一比较面板内重复试验会停止；IPD/系统综述/Meta等汇总不能标成RCT送入。不确定是否重叠时保留疑问，不自行造新ID绕过去重。程序依赖输入的身份与类型，不能替代原文核查。

RR、调整RR（aRR）、OR、aOR、HR、aHR、均值差MD和风险差RD分开绘制；不同结局、时点、人群、单位和置信水平亦分面。不能将aRR改名为RR或把OR当RR。比值类横轴为对数、参考线为1；差值类参考线为0。只有文献实际报告且已读到的estimate、ci_low、ci_high才能填入；缺区间则该条暂不绘制，不从事件数临时补算。

## 输入

UTF-8 JSON顶层仅有studies数组。每条必须具备下列字段，数值保持原文精度：

```json
{"studies":[{"study_id":"trial-a","label":"Trial A","study_type":"RCT","outcome":"Functional independence","timepoint":"90 days","population":"Selected adults","unit":"ratio","measure":"RR","ci_level":0.95,"estimate":1.2,"ci_low":0.9,"ci_high":1.5,"source":"Synthetic example, not clinical evidence","location":"Synthetic table 1"}]}
```

示例是合成数据，不能用于医学汇报。label是图上试验标签；source及location保留可核验来源。study_type仅接受RCT。unit必须取实际单位，RR等无量纲比值可写ratio；RD保持原文的比例或百分点单位，不自行换算。ci_level按小数填写（0.95，不是95）。图示不预设哪一方向有利：获益方向依据具体结局另行核实。

## 执行与交付

P为本次已探测的Python解释器，S为该Skill资源目录，W为本任务目录：

```bash
P S/scripts/render_study_forest.py --input W/studies.json --out-root W/forest-runs
```

返回唯一发布目录，内含figure.svg、300dpi figure.tiff、effects.csv、result.json、report.md、delivery.md、原输入input.json、实现与依赖快照、runtime-environment.json、reproduce.py及manifest.json。图和report.md复用同一组研究记录，报告逐项保留研究身份、人群、结局、时点、效应量类型、数值、置信区间及来源定位，不生成共同获益判断。摘要或PPT需引用结果时读取该报告，保留对应研究的终点及条件，不另写跨研究的统一增幅。原文支持及临床综合仍须另核，不能用“全部阳性”等概括代替逐项证据。

直接采用同次图与报告，不另写合并计算或改图面数字。保留原图与完整运行目录，打包ZIP并present_files主图、report.md及包；最终交付说明使用同次delivery.md。结果对象仅为转录与绘图回执，不证明原文真实或方案已获批准。

实际执行返回目录中的reproduce.py（同一P，--out-root给新目录），核对studies及effects.csv，再查看实际图面、标签、区间、单位、DPI和可读性。自定义源文件、缺来源或无法读清的区间仍按未核实处理，不因程序成功而通过医学验收。
