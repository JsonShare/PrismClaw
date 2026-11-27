"""
CSR报告撰写工具包 (csr-report-writer)

按照ICH E3指南规范撰写临床研究报告（CSR），自动从统计分析结果中提取数据
填充到标准19章节结构中，支持中文NMPA提交和英文FDA提交两种模式。

模块说明：
- main.py: 主入口，CLI参数解析和流程编排
- csr_structure.py: ICH E3章节结构定义（19个核心模块）
- section_builders.py: 各章节内容构建器
- template_engine.py: 模板引擎（加载模板、占位符替换）
- csr_formatter.py: CSR格式化（页面布局、目录、引用格式）
- data_collector.py: 从共享数据桩收集所有上游分析结果
"""

__version__ = "1.0.0"
__author__ = "Medical Skills Team"
