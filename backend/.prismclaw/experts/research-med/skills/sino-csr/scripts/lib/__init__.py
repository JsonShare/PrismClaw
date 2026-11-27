"""
csr-report-writer/scripts/lib/__init__.py

本模块为 csr-report-writer 提供本地化的共享函数库，
不依赖外部 shared/ 目录。

嵌入的共享模块：
- stats: 统计函数（Cohen's d, η², Cramér's V, Bonferroni, BH, 置信区间）
- utils: 工具函数（格式化、JSON解析等）

注意：csr-report-writer 不使用 Plain Language 模板，故不嵌入 templates 模块。
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
]
