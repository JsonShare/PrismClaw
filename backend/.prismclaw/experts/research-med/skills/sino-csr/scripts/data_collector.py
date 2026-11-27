"""
数据收集器模块

从共享数据桩（<session-root>/session-{id}/）收集所有上游skills的输出文件，
检查文件可用性，为下游章节构建提供数据基础。
"""

import json
import os
from pathlib import Path
import os
_SESSION_ROOT = Path(os.environ.get("PRISCLAW_SESSION_ROOT", str(Path.cwd())))
from typing import Dict, List, Optional, Tuple


class DataCollector:
    """
    从共享数据桩收集上游分析结果

    Attributes:
    -----------
    context_path : Path
        数据桩根目录
    available_files : Dict[str, Path]
        已找到的文件映射
    missing_files : List[str]
        未找到的文件列表
    """

    # 定义所有可能的数据文件及其来源skill
    EXPECTED_FILES = {
        # 来自 data-preparation
        "prepared_data": {
            "pattern": "01_prepared_data.csv",
            "source": "data-preparation",
            "description": "清洗后的数据",
            "required": False,
        },
        "data_dictionary": {
            "pattern": "02_data_dictionary.md",
            "source": "data-preparation",
            "description": "数据字典",
            "required": False,
        },
        "quality_report": {
            "pattern": "04_quality_report.md",
            "source": "data-preparation",
            "description": "数据质量报告",
            "required": False,
        },
        # 来自 differential-analysis
        "differential_results": {
            "pattern": "10_differential_results.json",
            "source": "differential-analysis",
            "description": "差异分析结果",
            "required": False,
        },
        # 来自 regression-analysis
        "regression_results": {
            "pattern": "11_regression_results.json",
            "source": "regression-analysis",
            "description": "回归分析结果",
            "required": False,
        },
        # 来自 survival-analysis
        "survival_results": {
            "pattern": "12_survival_results.json",
            "source": "survival-analysis",
            "description": "生存分析结果",
            "required": False,
        },
        # 来自 power-analysis
        "power_analysis": {
            "pattern": "power_analysis_result.md",
            "source": "power-analysis",
            "description": "样本量计算结果",
            "required": False,
        },
        # 来自 result-interpreter
        "plain_language": {
            "pattern": "plain_language_interpretation.md",
            "source": "result-interpreter",
            "description": "Plain Language解释",
            "required": False,
        },
        # 来自 chart-generator
        "charts_dir": {
            "pattern": "30_charts",
            "source": "chart-generator",
            "description": "图表目录",
            "required": False,
            "is_dir": True,
        },
    }

    def __init__(self, context_path: Path):
        """
        初始化数据收集器

        Parameters:
        -----------
        context_path : Path
            数据桩根目录路径
        """
        self.context_path = Path(context_path)
        self.available_files: Dict[str, Path] = {}
        self.missing_files: List[str] = []

    def collect_all(self) -> Dict[str, Path]:
        """
        扫描数据桩目录，收集所有可用的上游输出文件

        Returns:
        --------
        Dict[str, Path]: key为文件类型，value为文件路径
        """
        self.available_files = {}
        self.missing_files = []

        for key, file_info in self.EXPECTED_FILES.items():
            pattern = file_info["pattern"]
            file_path = self.context_path / pattern

            if file_info.get("is_dir", False):
                # 目录类型
                if file_path.is_dir():
                    self.available_files[key] = file_path
                else:
                    self.missing_files.append(key)
            else:
                # 文件类型
                if file_path.exists():
                    self.available_files[key] = file_path
                else:
                    self.missing_files.append(key)

        return self.available_files

    def get_file_path(self, key: str) -> Optional[Path]:
        """获取指定类型文件的路径"""
        return self.available_files.get(key)

    def is_available(self, key: str) -> bool:
        """检查指定类型文件是否可用"""
        return key in self.available_files

    def read_json(self, key: str) -> Optional[dict]:
        """
        读取JSON格式数据文件

        Parameters:
        -----------
        key : str
            文件类型标识

        Returns:
        --------
        Optional[dict]: 解析后的JSON数据，文件不存在则返回None
        """
        file_path = self.available_files.get(key)
        if not file_path or not file_path.exists():
            return None

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            print(f"  警告：无法解析 {file_path}，错误: {e}")
            return None

    def read_text(self, key: str) -> Optional[str]:
        """
        读取文本格式数据文件（md/txt等）

        Parameters:
        -----------
        key : str
            文件类型标识

        Returns:
        --------
        Optional[str]: 文件内容，文件不存在则返回None
        """
        file_path = self.available_files.get(key)
        if not file_path or not file_path.exists():
            return None

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return f.read()
        except UnicodeDecodeError as e:
            print(f"  警告：无法读取 {file_path}，错误: {e}")
            return None

    def get_chart_files(self) -> List[Path]:
        """
        获取所有图表文件列表

        Returns:
        --------
        List[Path]: 图表文件路径列表
        """
        charts_dir = self.available_files.get("charts_dir")
        if not charts_dir or not charts_dir.is_dir():
            return []

        chart_files = []
        for ext in [".tiff", ".tif", ".eps", ".pdf", ".png", ".svg"]:
            chart_files.extend(charts_dir.glob(f"*{ext}"))

        return sorted(chart_files)

    def get_availability_report(self) -> str:
        """
        生成数据可用性报告

        Returns:
        --------
        str: 可读的数据可用性报告
        """
        lines = ["数据可用性报告："]
        lines.append("-" * 50)

        for key, file_info in self.EXPECTED_FILES.items():
            source = file_info["source"]
            desc = file_info["description"]
            pattern = file_info["pattern"]

            if key in self.available_files:
                lines.append(f"  [已找到] {desc} ({key})")
                lines.append(f"         来源: {source} | 路径: {pattern}")
            else:
                lines.append(f"  [未找到] {desc} ({key})")
                lines.append(f"         来源: {source} | 预期路径: {pattern}")

        if self.missing_files:
            lines.append("")
            lines.append(f"缺失文件数: {len(self.missing_files)} / {len(self.EXPECTED_FILES)}")

            # 列出推荐的上游skill调用命令
            missing_sources = set()
            for key in self.missing_files:
                source = self.EXPECTED_FILES[key]["source"]
                missing_sources.add(source)

            if missing_sources:
                lines.append("")
                lines.append("建议先执行以下上游skills：")
                source_commands = {
                    "data-preparation": "/data-preparation --session_id {session_id} --mode quick",
                    "differential-analysis": "/differential-analysis --session_id {session_id} --groupby_vars treatment",
                    "regression-analysis": "/regression-analysis --session_id {session_id} --method logistic",
                    "survival-analysis": "/survival-analysis --session_id {session_id} --time_var os_time --event_var os_event",
                    "power-analysis": "/power-analysis --session_id {session_id} --test_method two_sample_ttest",
                    "result-interpreter": "/result-interpreter --session_id {session_id} --results_path 10_differential_results.json",
                    "chart-generator": "/chart-generator --session_id {session_id} --chart_type bar --journal_style Lancet",
                }
                for source in sorted(missing_sources):
                    cmd = source_commands.get(source, f"/{source}")
                    lines.append(f"  - {cmd}")

        return "\n".join(lines)

    def get_sample_size_from_data(self) -> Optional[int]:
        """
        从清洗后数据中获取样本量

        Returns:
        --------
        Optional[int]: 总样本量，数据不可用时返回None
        """
        if not self.is_available("prepared_data"):
            return None

        # 尝试用pandas读取
        try:
            import pandas as pd
            df = pd.read_csv(self.available_files["prepared_data"])
            return len(df)
        except ImportError:
            # pandas不可用，尝试按行计数
            try:
                file_path = self.available_files["prepared_data"]
                with open(file_path, "r", encoding="utf-8") as f:
                    return sum(1 for line in f) - 1  # 减去表头
            except Exception:
                return None
