"""
sino-report/lib - 本地共享模块副本
此目录包含从 shared/ 复制的共享代码，确保 Skill 完全自包含
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
    TTEST_INTERPRETATION,
    CHI2_INTERPRETATION,
    CORRELATION_INTERPRETATION,
    interpret_ttest_result,
    interpret_chi2_result,
    interpret_correlation,
)

__all__ = [
    # stats
    'cohens_d',
    'cohens_d_interpretation',
    'eta_squared',
    'cramers_v',
    'rank_biserial',
    'bonferroni_correction',
    'benjamini_hochberg',
    'calculate_95ci',
    # utils
    'ensure_dir',
    'get_file_hash',
    'format_number',
    'format_pvalue',
    'format_ci',
    'parse_json_safe',
    'timestamp_now',
    'truncate_string',
    # templates
    'TTEST_INTERPRETATION',
    'CHI2_INTERPRETATION',
    'CORRELATION_INTERPRETATION',
    'interpret_ttest_result',
    'interpret_chi2_result',
    'interpret_correlation',
]
