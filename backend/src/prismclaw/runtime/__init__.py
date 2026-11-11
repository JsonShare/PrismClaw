"""运行时服务 + engine 门面

本包包含两类代码：
1. 运行时服务：系统启动后持续运行的编排服务（memory_aftermath, capability_refresh, skills 等）
2. engine re-export 门面（facade/）：app 层通过此模块访问 engine 能力，不直接 import engine
"""
