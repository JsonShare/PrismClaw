# 受测描述统计程序

CSV必须UTF-8，包含group、value两列；可选id列，有id时(group,id)重复即停止。空值、非数值、NaN/Inf、非有限衍生量、n<2均停止，不静默清洗；是否负值合理由医生方案判定，程序不普遍禁止负值。

方案JSON示例：
```json
{"design":"independent","method":"descriptive","unit":"mg/L","error_bar":"SD","missing":"stop"}
```

已知配对设计不能伪装成independent；程序只能验证声明，不会替代研究设计核查。运行：
`<Python绝对路径> <技能目录>/scripts/describe.py --csv <输入.csv> --plan <方案.json> --out-root <输出根目录>`

程序仅使用Python标准库，创建唯一目录，输出result.json、summary.csv、mean_sd.svg与manifest.json；同目录保留输入原始字节及方案。SD采用样本SD(n-1)，不进行推断。程序有输入上限以避免无界载入。返回0后检查manifest与结果，再使用实际可用present_files工具交付；失败仅报告错误，不能临场重写算法绕过门槛。

脚本的拒绝覆盖只保护按本入口发布的文件，其他写工具仍能改文件。操作系统/应用负责只读输入挂载、包安装策略、命令白名单/沙箱、只写新版本的产物服务及历史会话版本绑定。未落实前不能声称这些是系统强制保证。

请求PNG时，先只读确认同一解释器已有matplotlib，再加 `--png`；缺库停止，不安装。PNG与SVG同用结果对象、均值±样本SD，不能改变误差线口径。

第三版单入口修订同时输出受测describe.py、内部_artifact_store.py及reproduce.py，manifest记录代码指纹；复现入口使用同目录快照并要求另给输出根，不覆盖原运行。
