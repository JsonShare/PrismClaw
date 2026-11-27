#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
多重比较校正模块

提供Bonferroni校正、Benjamini-Hochberg（BH）校正等
"""
from typing import Dict, List, Tuple
import numpy as np


class MultipleComparisonCorrector:
    """
    多重比较校正器

    提供多种多重比较校正方法
    """

    def __init__(self):
        """初始化校正器"""
        pass

    def bonferroni_correction(self, p_values: List[float]) -> List[float]:
        """
        Bonferroni校正（最保守的方法）

        原理：将显著性阈值α除以比较次数n
        优点：最简单、最保守
        缺点：容易产生Type II错误（假阴性）

        参数：
            p_values: 原始p值列表

        返回：
            List[float]: 校正后的p值
        """
        n = len(p_values)
        if n == 0:
            return []

        corrected = []
        for p in p_values:
            p_corrected = min(p * n, 1.0)
            corrected.append(p_corrected)

        return corrected

    def benjamini_hochberg(
        self,
        p_values: List[float],
        fdr: float = 0.05
    ) -> List[float]:
        """
        Benjamini-Hochberg（BH）校正 - 控制假发现率

        原理：控制期望的假发现率（FDR）而非家族误差率
        优点：比Bonferroni更 Powerful，保留更多发现
        适用范围：多重假设检验探索性研究

        参数：
            p_values: 原始p值列表
            fdr: 目标假发现率，默认0.05

        返回：
            List[float]: 校正后的p值（q值）
        """
        n = len(p_values)
        if n == 0:
            return []

        p_values = np.array(p_values)

        # 获取排序索引（升序）
        sorted_indices = np.argsort(p_values)
        sorted_p = p_values[sorted_indices]

        # 计算BH临界值
        # q_i = p_i * n / rank_i
        ranks = np.arange(1, n + 1)
        q_values = sorted_p * n / ranks

        # 从大到小处理，确保q值单调非增
        q_values_descending = np.maximum.accumulate(q_values[::-1])[::-1]

        # 映射回原始顺序
        q_corrected = np.zeros(n)
        for i, idx in enumerate(sorted_indices):
            q_corrected[idx] = q_values_descending[i]

        # 确保q值不超过1
        q_corrected = np.minimum(q_corrected, 1.0)

        return q_corrected.tolist()

    def benjamini_yekutieli(
        self,
        p_values: List[float],
        fdr: float = 0.05
    ) -> List[float]:
        """
        Benjamini-Yekutieli（BY）校正 - BH的保守版本

        适用于检验之间存在相关或依赖的情况

        参数：
            p_values: 原始p值列表
            fdr: 目标假发现率

        返回：
            List[float]: 校正后的p值
        """
        n = len(p_values)
        if n == 0:
            return []

        # 计算调和级数近似（用于依赖情况）
        c_n = np.sum(1 / np.arange(1, n + 1))  # 近似于 log(n) + gamma

        p_values = np.array(p_values)

        sorted_indices = np.argsort(p_values)
        sorted_p = p_values[sorted_indices]

        ranks = np.arange(1, n + 1)
        # BY: q_i = p_i * n * c_n / rank_i
        q_values = sorted_p * n * c_n / ranks

        # 确保单调非增
        q_values_descending = np.maximum.accumulate(q_values[::-1])[::-1]

        # 映射回原始顺序
        q_corrected = np.zeros(n)
        for i, idx in enumerate(sorted_indices):
            q_corrected[idx] = q_values_descending[i]

        q_corrected = np.minimum(q_corrected, 1.0)

        return q_corrected.tolist()

    def holm_bonferroni(
        self,
        p_values: List[float],
        alpha: float = 0.05
    ) -> Tuple[List[float], List[int]]:
        """
        Holm-Bonferroni方法（逐步方法，比简单Bonferroni更Powerful）

        原理：从最小的p值开始，逐步增加校正因子

        参数：
            p_values: 原始p值列表
            alpha: 显著性水平

        返回：
            Tuple[List[float], List[int]]: (校正后p值, 被拒绝的假设索引)
        """
        n = len(p_values)
        if n == 0:
            return [], []

        p_values = np.array(p_values)

        # 获取排序索引
        sorted_indices = np.argsort(p_values)
        sorted_p = p_values[sorted_indices]

        # Holm校正
        # 第i小的p值，校正因子为 n - i + 1
        ranks = np.arange(1, n + 1)
        corrected_sorted = np.minimum(sorted_p * (n - ranks + 1), 1.0)

        # 确保单调非增
        corrected_sorted = np.maximum.accumulate(corrected_sorted[::-1])[::-1]

        # 映射回原始顺序
        corrected = np.zeros(n)
        for i, idx in enumerate(sorted_indices):
            corrected[idx] = corrected_sorted[i]

        # 确定拒绝的假设
        rejected = [i for i in range(n) if corrected[i] < alpha]

        return corrected.tolist(), rejected

    def tukehs_method(
        self,
        p_values: List[float],
        q: float = 0.05
    ) -> Tuple[List[bool], float]:
        """
        Tukey's HSD方法（用于成对比较）

        适用于所有组之间成对比较的情况

        参数：
            p_values: 原始p值列表（必须为成对比较的数量）
            q: 家族误差率

        返回：
            Tuple[List[bool], float]: (每个比较是否显著, 临界学生化范围值)
        """
        n_comparisons = len(p_values)
        if n_comparisons == 0:
            return [], 0.0

        # Tukey方法需要知道组数
        # 从比较数推算组数：k*(k-1)/2 = n_comparisons
        # k = (1 + sqrt(1 + 8*n_comparisons)) / 2
        k = (1 + np.sqrt(1 + 8 * n_comparisons)) / 2

        if k != int(k):
            return [False] * n_comparisons, 0.0

        k = int(k)

        # 学生化范围q分布的临界值（近似）
        # 这里使用简化的近似，实际应用中应查表或使用精确计算
        df_within = 100  # 假设较大的自由度
        q_critical = self._tukey_critical_value(k, df_within, q)

        # 计算每个比较的q统计量（这里简化处理）
        # 实际需要样本均值和标准误
        significant = [p < q / n_comparisons for p in p_values]

        return significant, float(q_critical)

    def _tukey_critical_value(
        self,
        k: int,
        df: int,
        alpha: float
    ) -> float:
        """
        近似计算Tukey HSD临界值

        参数：
            k: 组数
            df: 自由度
            alpha: 显著性水平

        返回：
            float: 临界值（近似）
        """
        # 使用Studentized Range Distribution近似
        # 简化版本，实际应用应查表
        from scipy import stats

        try:
            # 使用scipy的studentized_range
            q_crit = stats.studentized_range.ppf(1 - alpha, k, df)
            return q_crit
        except:
            # 备用近似公式
            return np.sqrt(2) * stats.t.ppf(1 - alpha / 2, df)

    def report_correction_summary(
        self,
        p_values: List[float],
        method: str = 'BH',
        alpha: float = 0.05
    ) -> Dict:
        """
        生成多重比较校正摘要报告

        参数：
            p_values: 原始p值列表
            method: 校正方法
            alpha: 显著性水平

        返回：
            Dict: 摘要报告
        """
        if method == 'Bonferroni':
            corrected = self.bonferroni_correction(p_values)
        elif method == 'BH':
            corrected = self.benjamini_hochberg(p_values, fdr=alpha)
        elif method == 'BY':
            corrected = self.benjamini_yekutieli(p_values, fdr=alpha)
        else:
            corrected = p_values

        n_tests = len(p_values)
        n_significant_raw = sum(1 for p in p_values if p < alpha)
        n_significant_corrected = sum(1 for p in corrected if p < alpha)

        return {
            'method': method,
            'n_tests': n_tests,
            'alpha': alpha,
            'n_significant_raw': n_significant_raw,
            'n_significant_corrected': n_significant_corrected,
            'p_values_raw': p_values,
            'p_values_corrected': corrected,
            'significant_comparisons': [
                i for i, p in enumerate(corrected) if p < alpha
            ]
        }