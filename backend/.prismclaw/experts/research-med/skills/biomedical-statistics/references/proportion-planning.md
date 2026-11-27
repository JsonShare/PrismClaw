# 单总体比例的精度法规划

沿用仓库calc-sample-size参考中已有的比例方差公式：`n = ceil(z_(1-α/2)^2 p(1-p)/d^2)`。这里p是待估发生率，d是绝对误差；不是诊断准确度研究中的患病率换算，也不额外除以p。用途是独立观测、大总体近似下的发生率精度规划，不是两个比例比较、检验效能、复杂抽样或多变量预测模型样本量。

方法学资料：[WHO，Lwanga与Lemeshow，1991，Sample size determination in health studies: a practical manual](https://iris.who.int/bitstream/handle/10665/40062/9241544058_%28p1-p22%29.pdf?sequence=1)，按“Estimating a population proportion with specified absolute precision”核对设计和参数。书目信息不证明任何特定患者或数据集满足该假设。

使用同一已确认Python解释器运行，参数均取本次原输入：

`<Python绝对路径> <resourceBase>/scripts/proportion_sample_size.py --p <预期率> --absolute-margin <绝对误差> --alpha <双侧显著性水平> --format markdown`

Markdown报告由同一计算结果生成，直接作为本次公式、参数、结果与适用边界的正文，不再自由增加另一组样本量示例、相对误差例数、EPV规则、事件数或可容纳变量数。需要机器读取时用默认JSON，保留真实stdout和退出码即可。原题未指定实际抽样方式，不得假定单中心、连续入组或便利抽样。

数值只按指定精度目标计算。未给失访校正、有限总体、抽样设计或回归规划参数时，明确未纳入，不填“常规值”，不额外展示这些部分的公式和数量。不要用一个分支的例数解释另一个没有完整参数的设计。

程序不联网、不读取附件、不安装依赖、不写文件。保留本次调用及stdout即可复现；用户要求文件时将结果放当前任务新目录。失败如实说明，不临时改写算法或换无关解释器。
