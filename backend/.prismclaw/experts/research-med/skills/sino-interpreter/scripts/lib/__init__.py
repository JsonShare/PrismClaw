"""
result-interpreter/scripts/lib/__init__.py

本模块为 result-interpreter 提供本地化的共享函数库，
不依赖外部 shared/ 目录。

嵌入的共享模块：
- stats: 统计函数（Cohen's d, η², Cramér's V, Bonferroni, BH, 置信区间）
- utils: 工具函数（格式化、JSON解析等）
- templates: Plain Language 解释模板（作为本地 template_engine.py 的补充）
"""

from .stats import (
    cohens_d,
    cohens_d_interpretation,
    eta_squared,
    cramers_v,
    rank_biserial,
    bonferroni_correction,
    benjamini_hochberg,
    calculate_95ci,
)

from .utils import (
    ensure_dir,
    get_file_hash,
    format_number,
    format_pvalue,
    format_ci,
    parse_json_safe,
    timestamp_now,
    truncate_string,
)

from .templates import (
    interpret_ttest_result,
    interpret_chi2_result,
    interpret_correlation,
    TTEST_INTERPRETATION,
    CHI2_INTERPRETATION,
    CORRELATION_INTERPRETATION,
)

__all__ = [
    # stats
    "cohens_d",
    "cohens_d_interpretation",
    "eta_squared",
    "cramers_v",
    "rank_biserial",
    "bonferroni_correction",
    "benjamini_hochberg",
    "calculate_95ci",
    # utils
    "ensure_dir",
    "get_file_hash",
    "format_number",
    "format_pvalue",
    "format_ci",
    "parse_json_safe",
    "timestamp_now",
    "truncate_string",
    # templates
    "interpret_ttest_result",
    "interpret_chi2_result",
    "interpret_correlation",
    "TTEST_INTERPRETATION",
    "CHI2_INTERPRETATION",
    "CORRELATION_INTERPRETATION",
]
