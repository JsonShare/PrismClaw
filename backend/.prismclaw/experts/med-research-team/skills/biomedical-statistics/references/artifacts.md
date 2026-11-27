# 统计产物发布

`scripts/analyze.py`和旧独立组描述入口`scripts/describe.py`（见[执行契约](execution.md)）都直接发布唯一运行目录，含输入快照、方案、结果、图、实现快照、复现入口和manifest，并输出该目录绝对路径。不要再次发布、复制子集、给清单改名或另调通用发布器；内部`_artifact_store.py`没有命令行入口。

analyze.py的交付文件是图（SVG、TIFF）、report.md和reproduction.zip，用present_files逐个提交真实文件，遵守单次文件数上限。reproduction.zip已包含输入、方案、实现、依赖要求、原结果和reproduce.py，运行时已在临时目录重跑核对；不要只复制reproduce.py，也不要另打一个包。说明、日志或额外代码另建伴随目录，不改已发布目录。

复现命令：`<同一解释器> <运行目录>/reproduce.py --out-root <新的输出根目录>`。包不捆绑Python和第三方依赖，接收方仍需满足requirements.txt。

程序只约束这个入口，其他写文件工具仍能改动文件。UUID与哈希不是权限或不可变存储；安装禁止、跨任务隔离和输入白名单需由应用执行代理落实，本轮不声称已具备。
