"""
shared/stats/__init__.py - 统计函数库

提供效应量计算、多重比较校正等统计函数。
本文件已嵌入 result-interpreter/scripts/lib/，完全自包含。

来源：shared/stats/__init__.py
版本：1.0.0
"""

import numpy as np
from scipy import stats
from typing import Tuple, List


def cohens_d(group1: np.ndarray, group2: np.ndarray) -> float:
    """
    计算Cohen's d效应量

    Args:
        group1: 第一组数据
        group2: 第二组数据

    Returns:
        Cohen's d值
    """
    n1, n2 = len(group1), len(group2)
    var1, var2 = np.var(group1, ddof=1), np.var(group2, ddof=1)

    # 合并标准差
    pooled_std = np.sqrt(((n1 - 1) * var1 + (n2 - 1) * var2) / (n1 + n2 - 2))

    # 计算Cohen's d
    d = (np.mean(group1) - np.mean(group2)) / pooled_std
    return float(d)


def cohens_d_interpretation(d: float) -> str:
    """
    Cohen's d解释

    Args:
        d: Cohen's d值

    Returns:
        解释文本
    """
    d = abs(d)
    if d < 0.2:
        return "微小效应"
    elif d < 0.5:
        return "小效应"
    elif d < 0.8:
        return "中等效应"
    else:
        return "大效应"


def eta_squared(f_stat: float, df_between: int, df_within: int) -> float:
    """
    计算ANOVA的η²效应量

    Args:
        f_stat: F统计量
        df_between: 组间自由度
        df_within: 组内自由度

    Returns:
        η²值
    """
    return float(f_stat * df_between) / float(f_stat * df_between + df_within)


def cramers_v(chi2: float, n: int, min_dim: int) -> float:
    """
    计算Cramér's V关联强度

    Args:
        chi2: 卡方统计量
        n: 样本量
        min_dim: 行数或列数的最小值（取行列数-1的较小者）

    Returns:
        Cramér's V值
    """
    return float(np.sqrt(chi2 / (n * (min_dim))))


def rank_biserial(group1: np.ndarray, group2: np.ndarray) -> float:
    """
    计算等级二列相关系数（用于Mann-Whitney U检验）

    Args:
        group1: 第一组数据
        group2: 第二组数据

    Returns:
        r值
    """
    n1, n2 = len(group1), len(group2)

    # 计算U统计量
    u_stat, _ = stats.mannwhitneyu(group1, group2, alternative='two-sided')

    # 转换为等级二列相关系数
    r = 1 - (2 * u_stat) / (n1 * n2)
    return float(r)


def bonferroni_correction(p_values: List[float], alpha: float = 0.05) -> Tuple[List[float], List[bool]]:
    """
    Bonferroni多重比较校正

    Args:
        p_values: p值列表
        alpha: 显著性水平

    Returns:
        (校正后的p值, 是否拒绝原假设)
    """
    n = len(p_values)
    corrected = [min(p * n, 1.0) for p in p_values]
    rejected = [p < alpha for p in corrected]
    return corrected, rejected


def benjamini_hochberg(p_values: List[float], fdr: float = 0.05) -> Tuple[List[float], List[int]]:
    """
    Benjamini-Hochberg FDR校正

    Args:
        p_values: p值列表
        fdr: 错误发现率

    Returns:
        (每个p值对应的BH临界值, 拒绝原假设的最大索引)
    """
    n = len(p_values)
    sorted_indices = np.argsort(p_values)
    sorted_p = np.array(p_values)[sorted_indices]

    # 计算每个位置的BH临界值
    bh_critical = [(i + 1) / n * fdr for i in range(n)]

    # 找到最大的满足条件的索引
    max_idx = 0
    for i in range(n):
        if sorted_p[i] <= bh_critical[i]:
            max_idx = i + 1

    # 返回原始顺序的结果
    original_rejected = [0] * n
    for i in range(max_idx):
        original_rejected[sorted_indices[i]] = 1

    return bh_critical, original_rejected


def calculate_95ci(mean: float, std: float, n: int) -> Tuple[float, float]:
    """
    计算95%置信区间

    Args:
        mean: 均值
        std: 标准差
        n: 样本量

    Returns:
        (下限, 上限)
    """
    se = std / np.sqrt(n)
    t_val = stats.t.ppf(0.975, n - 1)
    return float(mean - t_val * se), float(mean + t_val * se)
