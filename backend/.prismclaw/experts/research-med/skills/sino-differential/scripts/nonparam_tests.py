#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
非参数检验模块

提供Mann-Whitney U检验、Wilcoxon符号秩检验、Kruskal-Wallis检验等
"""
from typing import Dict, List, Tuple, Union
import numpy as np
from scipy import stats


class NonparametricTests:
    """
    非参数检验类

    提供Mann-Whitney U、Wilcoxon符号秩、Kruskal-Wallis、Friedman等检验
    """

    def __init__(self, alpha: float = 0.05, confidence_level: float = 0.95):
        """
        初始化非参数检验器

        参数：
            alpha: 显著性水平
            confidence_level: 置信水平
        """
        self.alpha = alpha
        self.confidence_level = confidence_level

    def mann_whitney_u(
        self,
        group1: Union[List, np.ndarray],
        group2: Union[List, np.ndarray],
        alternative: str = 'two-sided'
    ) -> Dict:
        """
        Mann-Whitney U检验（非参数两样本检验）

        参数：
            group1: 第一组数据
            group2: 第二组数据
            alternative: 备择假设类型，'two-sided', 'less', 'greater'

        返回：
            Dict: 检验结果
        """
        group1 = np.array(group1)
        group2 = np.array(group2)

        # 去除缺失值
        group1 = group1[~np.isnan(group1)]
        group2 = group2[~np.isnan(group2)]

        n1, n2 = len(group1), len(group2)

        if n1 < 1 or n2 < 1:
            return {
                'statistic': None,
                'p_value': None,
                'error': '样本量不足'
            }

        # 执行Mann-Whitney U检验
        statistic, p_value = stats.mannwhitneyu(group1, group2, alternative=alternative)

        # 计算秩均值
        all_data = np.concatenate([group1, group2])
        ranks = stats.rankdata(all_data)
        rank1 = np.mean(ranks[:n1])
        rank2 = np.mean(ranks[n1:])

        # 计算效应量 r = Z / sqrt(N)
        # Z值近似计算
        N = n1 + n2
        mean_rank = (N + 1) / 2
        # U统计量的标准化
        se = np.sqrt(n1 * n2 * (N + 1) / 12)
        if se > 0:
            z_approx = (statistic - n1 * n2 / 2) / se
        else:
            z_approx = 0

        return {
            'statistic': float(statistic),
            'U': float(statistic),
            'p_value': float(p_value),
            'n1': n1,
            'n2': n2,
            'mean_rank1': float(rank1),
            'mean_rank2': float(rank2),
            'z_approx': float(z_approx),
            'interpretation': self._interpret_result(p_value, 'Mann-Whitney U检验')
        }

    def wilcoxon_signed_rank(
        self,
        paired1: Union[List, np.ndarray],
        paired2: Union[List, np.ndarray],
        alternative: str = 'two-sided'
    ) -> Dict:
        """
        Wilcoxon符号秩检验（配对样本的非参数检验）

        参数：
            paired1: 第一组配对数据
            paired2: 第二组配对数据
            alternative: 备择假设类型

        返回：
            Dict: 检验结果
        """
        paired1 = np.array(paired1)
        paired2 = np.array(paired2)

        # 去除缺失值（保留配对）
        mask = ~np.isnan(paired1) & ~np.isnan(paired2)
        paired1 = paired1[mask]
        paired2 = paired2[mask]

        n = len(paired1)

        if n < 5:
            return {
                'statistic': None,
                'p_value': None,
                'error': f'配对样本数太少(n={n})，Wilcoxon检验需要至少5对'
            }

        diff = paired1 - paired2

        # 去除差值为0的配对
        nonzero_mask = diff != 0
        diff_nonzero = diff[nonzero_mask]
        n_nonzero = len(diff_nonzero)

        if n_nonzero < 5:
            return {
                'statistic': None,
                'p_value': None,
                'error': f'非零差值对太少(n={n_nonzero})'
            }

        # 计算Wilcoxon符号秩统计量
        ranks = stats.rankdata(np.abs(diff_nonzero))
        sign = np.sign(diff_nonzero)

        # W+ = 正差值的秩和，W- = 负差值的秩和
        w_plus = np.sum(ranks[sign > 0])
        w_minus = np.sum(ranks[sign < 0])

        # 取较小的作为统计量
        statistic = min(w_plus, w_minus)

        # scipy的wilcoxon返回的statistic是W+
        w_scipy, p_value = stats.wilcoxon(paired1, paired2, alternative=alternative)

        return {
            'statistic': float(statistic),
            'W_plus': float(w_plus),
            'W_minus': float(w_minus),
            'p_value': float(p_value),
            'n': n,
            'n_nonzero': n_nonzero,
            'interpretation': self._interpret_result(p_value, 'Wilcoxon符号秩检验')
        }

    def kruskal_wallis(self, *groups: Union[List, np.ndarray]) -> Dict:
        """
        Kruskal-Wallis检验（非参数多组比较）

        参数：
            *groups: 多个数据组

        返回：
            Dict: 检验结果
        """
        # 转换为numpy数组并去除缺失值
        valid_groups = []
        for group in groups:
            group = np.array(group)
            group = group[~np.isnan(group)]
            if len(group) > 0:
                valid_groups.append(group)

        if len(valid_groups) < 2:
            return {
                'statistic': None,
                'df': None,
                'p_value': None,
                'error': '组数不足'
            }

        # 执行Kruskal-Wallis检验
        statistic, p_value = stats.kruskal(*valid_groups)

        # 自由度
        df = len(valid_groups) - 1

        # 各组样本量和秩均值
        all_data = np.concatenate(valid_groups)
        ranks = stats.rankdata(all_data)

        group_ranks = []
        start = 0
        for g in valid_groups:
            n = len(g)
            group_ranks.append(np.mean(ranks[start:start+n]))
            start += n

        return {
            'statistic': float(statistic),
            'df': df,
            'p_value': float(p_value),
            'group_ns': [len(g) for g in valid_groups],
            'group_mean_ranks': [float(r) for r in group_ranks],
            'interpretation': self._interpret_result(p_value, 'Kruskal-Wallis检验')
        }

    def friedman_test(self, *groups: Union[List, np.ndarray]) -> Dict:
        """
        Friedman检验（重复测量非参数检验）

        参数：
            *groups: 多个时间点/条件的配对数据

        返回：
            Dict: 检验结果
        """
        # 转换为numpy数组并去除缺失值
        valid_groups = []
        min_len = float('inf')

        for group in groups:
            group = np.array(group)
            group = group[~np.isnan(group)]
            if len(group) > 0:
                valid_groups.append(group)
                min_len = min(min_len, len(group))

        if len(valid_groups) < 2:
            return {
                'statistic': None,
                'df': None,
                'p_value': None,
                'error': '条件数不足'
            }

        if min_len < 3:
            return {
                'statistic': None,
                'df': None,
                'p_value': None,
                'error': f'每个条件的样本量太少(min_n={min_len})'
            }

        # 截断到最小长度以保持配对
        truncated_groups = [g[:min_len] for g in valid_groups]

        # 执行Friedman检验
        statistic, p_value = stats.friedmanchisquare(*truncated_groups)

        df = len(valid_groups) - 1

        return {
            'statistic': float(statistic),
            'df': df,
            'p_value': float(p_value),
            'n_subjects': min_len,
            'n_conditions': len(valid_groups),
            'interpretation': self._interpret_result(p_value, 'Friedman检验')
        }

    def _interpret_result(self, p_value: float, method: str) -> str:
        """
        解释检验结果

        参数：
            p_value: p值
            method: 检验方法名

        返回：
            str: 解释文本
        """
        if p_value is None:
            return '无法计算p值'

        if p_value < 0.001:
            return f"差异有高度统计学意义（{method}, p<0.001）"
        elif p_value < self.alpha:
            return f"差异有统计学意义（{method}, p={p_value:.4f}<{self.alpha}）"
        else:
            return f"差异无统计学意义（{method}, p={p_value:.4f}>={self.alpha}）"