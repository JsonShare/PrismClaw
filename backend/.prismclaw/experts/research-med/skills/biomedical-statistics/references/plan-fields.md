# analyze.py 方案字段

只读与本题相关的小节。程序校验字段，有误时停止并说明原因。

## 输入

- CSV：UTF-8，必需group、value，可选panel、id、time；默认panel=analysis。配对和纵向数据必须有id，重复的(panel, group, time, id)停止。
- XLSX：用`blocks`逐块登记，程序只读选定工作表和范围。每块含sheet、panel、group、range；没有time_values时范围内的数值都属于该组。
- `missing`：`stop`遇空白停止；`omit`排除空白并记录位置。0和负值不排除，NaN、Inf和非法字符串不当空白。不静默清洗；不能为绕过stop缩短选区。
- 配对：每块给`ids`列表（只支持单列范围），两组ID集合须相同，不按顺序或行号猜配对。
- 纵向：块内加`"time_values":[0,2,4,6,8],"id_axis":"column"`，每列是同一对象跨行的观测，逐时点汇总，不送入独立组推断。
- 数值横轴（密度、剂量）：method用descriptive，每块用`x_cell`指向原表的数值表头，如`{"sheet":"Data","panel":"Cell line","group":"Control","range":"C4:C6","x_cell":"C3"}`；同一块的x_cell与time_values互斥。

## 坐标轴与标签

- `--plot`必须给`plot_labels.y`（指标名和单位，如`Body weight change (%)`）；时间图还要`plot_labels.x`（如`Time (days)`）。只有`%`或没有单位的Time不算完整轴名；来源未给单位写“单位未注明”，不写unitless或arbitrary units。
- XLSX时间图在顶层写`"plot_label_sources":{"x":{"sheet":"Data","cell":"A4"}}`，单元格取自本次表头，程序用该原文作横轴。多个时点标签可写`{"sheet":"Data","cells":["A3","A4"],"text":"Time (days)"}`。time_values仍须逐项对应原表时间列。
- 各面板指标或单位不同：用`plot_labels.y_by_panel`按面板给纵轴；相对、归一化或无量纲限定在`plot_label_sources.y_by_panel`按面板填原表单元格。

## 显示选项

- `plot_columns`：分面列数，1到面板数，最多10。
- `plot_raw_points=true`：在均值折线上叠加原始观测；图中自动标注各点有效n。
- `plot_blocks`：原题要求分块排列时使用，如`{"Marker":[{"label":"Early","groups":["Control-early","Treatment-early"]},{"label":"Late","groups":["Control-late","Treatment-late"]}]}`。组名与数据顺序一致、每组恰好出现一次；不重排观测，不当作研究设计，时间折线不用。分块只影响排列，不代表只转录块内比较。
- `significance_style`：默认`p`显示P值；用户明确要星号时用`p_and_stars`，保留原P值，按小于0.05、0.01、0.001三档，带不等号的P只标能确定的级别。
- 没有时间维度的面板有多项计算比较时，图下自动用上三角P值矩阵列出全部比较。
- `plot_rationale`：本次图形选择理由，报告单列，不附来源编号。
- `analysis_notes`：本轮输入冲突、实际用户澄清及未完成范围，字符串列表，报告单独成节，不挂原文来源。

## 输出

运行目录含输入快照、方案、result.json、summary.csv、comparisons.csv、omnibus.csv（ANOVA）、input-tidy.csv、figure.svg、figure.tiff（300 dpi RGB）、figure-preview.png、report.md、delivery.md、rendered-points.json、reproduction.zip、reproduction-check.json和manifest.json；用`--renderer`时另有render-variant.json、analysis-figure.*和analysis-result.json，见[同次发布最终重绘](renderer.md)。误差线固定为样本SD(n−1)。report.md逐行绑定比较的A、B组、A减B的差值方向、原始及调整后P值、区间和校正口径，Holm只调整P值，不把区间称为同步校正。

## 分析状态交接

组合任务向论文、PPT或申报材料交接时，写明研究对象及性质、真实输入定位、运行目录与实际方法、估计量和比较方向、可支持的结论及未执行范围。不改已发布的report.md或result.json补研究事实。原始变化值只支持对这些变化值的描述或前提已确认的检验，不补基线、随访时长或入组流程；已取得观测数不是总入组数或零脱落的证据。拟议模型缺变量、前提或登记实现时写“方案，尚未执行”及原因，不临时手写未支持的统计算法，不用已支持方法改名代替；Methods区分本次实际计算与未来计划，Results、译文和PPT只用本次实际结果。
