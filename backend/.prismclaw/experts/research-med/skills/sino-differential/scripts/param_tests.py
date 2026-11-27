#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
参数检验模块

提供t检验和ANOVA等参数检验方法
"""
from typing import Dict, List, Tuple, Union
import numpy as np
from scipy import stats


class ParametricTests:
    """
    参数检验类

    提供独立样本t检验、配对t检验、Welch检验、ANOVA等
    """

    def __init__(self, alpha: float = 0.05, confidence_level: float = 0.95):
        """
        初始化参数检验器

        参数：
            alpha: 显著性水平
            confidence_level: 置信水平
        """
        self.alpha = alpha
        self.confidence_level = confidence_level

    def independent_ttest(
        self,
        group1: Union[List, np.ndarray],
        group2: Union[List, np.ndarray],
        equal_var: bool = True
    ) -> Dict:
        """
        独立样本t检验

        参数：
            group1: 第一组数据
            group2: 第二组数据
            equal_var: 是否假设方差齐性（True则用标准t检验，False则用Welch检验）

        返回：
            Dict: 检验结果
        """
        group1 = np.array(group1)
        group2 = np.array(group2)

        # 去除缺失值
        group1 = group1[~np.isnan(group1)]
        group2 = group2[~np.isnan(group2)]

        n1, n2 = len(group1), len(group2)

        if n1 < 2 or n2 < 2:
            return {
                'statistic': None,
                'df': None,
                'p_value': None,
                'error': '样本量不足'
            }

        mean1, mean2 = np.mean(group1), np.mean(group2)
        var1, var2 = np.var(group1, ddof=1), np.var(group2, ddof=1)

        if equal_var:
            # 标准独立样本t检验
            df = n1 + n2 - 2
            pooled_var = ((n1 - 1) * var1 + (n2 - 1) * var2) / df
            se = np.sqrt(pooled_var * (1/n1 + 1/n2))
            statistic = (mean1 - mean2) / se
            p_value = 2 * (1 - stats.t.cdf(abs(statistic), df))
        else:
            # Welch's t检验
            se = np.sqrt(var1/n1 + var2/n2)
            statistic = (mean1 - mean2) / se
            # Welch-Satterthwaite 自由度近似
            num = (var1/n1 + var2/n2) ** 2
            denom = (var1/n1)**2/(n1-1) + (var2/n2)**2/(n2-1)
            df = num / denom
            p_value = 2 * (1 - stats.t.cdf(abs(statistic), df))

        # 计算均值差和置信区间
        mean_diff = mean1 - mean2
        t_crit = stats.t.ppf((1 + self.confidence_level) / 2, df)
        mean_diff_se = se
        ci_lower = mean_diff - t_crit * mean_diff_se
        ci_upper = mean_diff + t_crit * mean_diff_se

        return {
            'statistic': float(statistic),
            'df': float(df),
            'p_value': float(p_value),
            'mean1': float(mean1),
            'mean2': float(mean2),
            'mean_diff': float(mean_diff),
            'var1': float(var1),
            'var2': float(var2),
            'sd1': float(np.sqrt(var1)),
            'sd2': float(np.sqrt(var2)),
            'n1': n1,
            'n2': n2,
            'ci': (float(ci_lower), float(ci_upper)),
            'se': float(se),
            'interpretation': self._interpret_result(p_value, '独立样本t检验')
        }

    def welch_ttest(
        self,
        group1: Union[List, np.ndarray],
        group2: Union[List, np.ndarray]
    ) -> Dict:
        """
        Welch's t检验（方差不齐时使用）

        参数：
            group1: 第一组数据
            group2: 第二组数据

        返回：
            Dict: 检验结果
        """
        return self.independent_ttest(group1, group2, equal_var=False)

    def paired_ttest(
        self,
        paired1: Union[List, np.ndarray],
        paired2: Union[List, np.ndarray]
    ) -> Dict:
        """
        配对样本t检验

        参数：
            paired1: 第一组配对数据
            paired2: 第二组配对数据

        返回：
            Dict: 检验结果
        """
        paired1 = np.array(paired1)
        paired2 = np.array(paired2)

        # 去除缺失值（保留两组的配对）
        mask = ~np.isnan(paired1) & ~np.isnan(paired2)
        paired1 = paired1[mask]
        paired2 = paired2[mask]

        n = len(paired1)

        if n < 2:
            return {
                'statistic': None,
                'df': None,
                'p_value': None,
                'error': '配对样本数不足'
            }

        diff = paired1 - paired2
        mean_diff = np.mean(diff)
        sd_diff = np.std(diff, ddof=1)
        se_diff = sd_diff / np.sqrt(n)

        if sd_diff == 0:
            return {
                'statistic': 0,
                'df': n - 1,
                'p_value': 1.0,
                'mean_diff': float(mean_diff),
                'sd_diff': 0,
                'n': n,
                'ci': (float(mean_diff), float(mean_diff)),
                'interpretation': '两组数据完全相同，无差异'
            }

        statistic = mean_diff / se_diff
        df = n - 1
        p_value = 2 * (1 - stats.t.cdf(abs(statistic), df))

        # 置信区间
        t_crit = stats.t.ppf((1 + self.confidence_level) / 2, df)
        ci_lower = mean_diff - t_crit * se_diff
        ci_upper = mean_diff + t_crit * se_diff

        return {
            'statistic': float(statistic),
            'df': float(df),
            'p_value': float(p_value),
            'mean_diff': float(mean_diff),
            'sd_diff': float(sd_diff),
            'se_diff': float(se_diff),
            'n': n,
            'ci': (float(ci_lower), float(ci_upper)),
            'interpretation': self._interpret_result(p_value, '配对t检验')
        }

    def oneway_anova(self, *groups: Union[List, np.ndarray]) -> Dict:
        """
        单因素方差分析（One-way ANOVA）

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

        # 计算各组均值、样本量、方差
        means = [np.mean(g) for g in valid_groups]
        ns = [len(g) for g in valid_groups]
        variances = [np.var(g, ddof=1) for g in valid_groups]

        n_total = sum(ns)
        grand_mean = sum(np.mean(g) * len(g) for g in valid_groups) / n_total

        # 组间平方和 (SSB)
        ssb = sum(n * (m - grand_mean)**2 for n, m in zip(ns, means))

        # 组内平方和 (SSW)
        ssw = sum((n - 1) * v for n, v in zip(ns, variances))

        # 自由度
        df_between = len(valid_groups) - 1
        df_within = n_total - len(valid_groups)

        # 均方
        msb = ssb / df_between
        msw = ssw / df_within

        # F统计量
        if msw == 0:
            return {
                'statistic': float('inf'),
                'df': (df_between, df_within),
                'p_value': 0.0,
                'ssb': float(ssb),
                'ssw': float(ssw),
                'msb': float(msb),
                'msw': 0,
                'interpretation': '组内方差为零，各组数据完全相同'
            }

        f_stat = msb / msw
        p_value = 1 - stats.f.cdf(f_stat, df_between, df_within)

        return {
            'statistic': float(f_stat),
            'df': (df_between, df_within),
            'p_value': float(p_value),
            'ssb': float(ssb),
            'ssw': float(ssw),
            'msb': float(msb),
            'msw': float(msw),
            'group_means': [float(m) for m in means],
            'group_ns': ns,
            'interpretation': self._interpret_result(p_value, '单因素ANOVA')
        }

    def welch_anova(self, *groups: Union[List, np.ndarray]) -> Dict:
        """
        Welch's ANOVA（方差不齐时使用）

        参数：
            *groups: 多个数据组

        返回：
            Dict: 检验结果
        """
        # Welch's ANOVA 使用修正的平方和
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

        ns = [len(g) for g in valid_groups]
        means = [np.mean(g) for g in valid_groups]
        variances = [np.var(g, ddof=1) for g in valid_groups]
        n_total = sum(ns)

        # Welch's F 统计量
        weights = [n / v for n, v in zip(ns, variances) if v > 0]
        sum_weights = sum(weights)
        weighted_mean = sum(w * m for w, m in zip(weights, means)) / sum_weights

        # 修正的组间平方和
        ssb_star = sum(w * (m - weighted_mean)**2 for w, m in zip(weights, means))

        # 自由度（Welch-Satterthwaite近似）
        lambda_vals = [(1 - w/sum_weights)**2 / (n - 1) for w, n in zip(weights, ns)]
        df1 = len(valid_groups) - 1
        df2 = 1 / sum(lambda_vals) if sum(lambda_vals) > 0 else float('inf')

        # 修正的F统计量
        if sum_weights == 0:
            return {
                'statistic': 0,
                'df': (df1, df2),
                'p_value': 1.0,
                'method': "Welch's ANOVA",
                'interpretation': '数据不足'
            }

        f_stat = ssb_star * (n_total - len(valid_groups)) / (df1 * (n_total - 2))
        p_value = 1 - stats.f.cdf(f_stat, df1, df2)

        return {
            'statistic': float(f_stat),
            'df': (df1, float(df2)),
            'p_value': float(p_value),
            'method': "Welch's ANOVA",
            'interpretation': self._interpret_result(p_value, "Welch's ANOVA")
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
        if p_value < 0.001:
            return f"差异有高度统计学意义（{method}, p<0.001）"
        elif p_value < self.alpha:
            return f"差异有统计学意义（{method}, p={p_value:.4f}<{self.alpha}）"
        else:
            return f"差异无统计学意义（{method}, p={p_value:.4f}>={self.alpha}）"