# 原表P值转录

原表已给P值时只转录，不重新检验或调整；本次没有原表P列时才由程序计算。

## 字段

只有XLSX的descriptive或row_z_descriptive可用`reported_comparisons`。每项含panel、a、b、sheet、label_cell、p_cell和校正状态：

```json
{"panel":"Marker","a":"Group A","b":"Group B","sheet":"Data","label_cell":"G3","p_cell":"I3",
 "adjustment":"adjusted","adjustment_evidence":[{"kind":"input_cell","sheet":"Data","cell":"I2","quote":"Adjusted P Value"}]}
```

- a、b必须是本次数据中的组名；程序保留原比较标签和单元格来源，输出reported-comparisons.csv。
- `adjustment`取adjusted、unadjusted或unknown（旧写法adjusted=true等同adjusted）。adjusted或unadjusted必须附真实`adjustment_evidence`（input_cell、input_text或article_xml，格式同[图注来源说明](source-notes.md)），引文须确实说明该比较的校正状态；只有P列或检验名称时用unknown，图中只写P。
- `<0,0001`这类界限原样保留，图中写`Adjusted P<0.0001`，不改成精确值。缺失、非法值或比较组错误时程序停止。
- 存在时间或数值横轴时，每项还要`time_a`、`time_b`，端点由panel、组名和横坐标共同确定，支持同组不同时点。

## 覆盖与映射

- 用户要求整块转录时覆盖其中全部比较，包括跨时点、跨显示分块的比较，不删行绕过映射核实。用户确实只要指定子集时，用`reported_comparison_subset`记录真实`user_quote`和`locator`。
- 原比较标签写作`X vs Y`而本次组名不同，程序拒绝无依据的映射。先取得用户真实确认或本次原文依据，再在该项写`source_group_mapping`：`kind`为`user_confirmation`或`original_source`，`quote`为实际原句，`locator`为答复或原文位置，`aliases`把两个原标签精确对应到a、b。original_source的引句须包含映射两端的实际时间值和单位。不能靠顺序、行号或相同样本数推定，不得编造答复或引句。
- 有真实歧义时只问这个映射，不夹带单位、配色、误差条等已给口径；未确认的比较不标P值，其余部分照常交付。
- 来源明确的原P检验方法和校正方法写入caption_notes的original_method，与本次仅转录分开。
